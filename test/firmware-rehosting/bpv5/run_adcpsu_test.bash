#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# ADC + PSU + AMUX (analog subsystem) end-to-end test: boot the Bus Pirate v5
# firmware under HALucinator and exercise the *modeled* analog rails through the
# live CLI from the HiZ> shell:
#
#   'v'        -> measure voltage on ALL pins  (ui_info / amux_sweep path)
#                 -> firmware renders each IOn / VOUT / VREF in volts
#   'W 3.3'    -> enable the programmable supply at 3.3 V (psucmd -> psu_enable)
#                 -> firmware reports the set voltage back
#   'v'        -> measure again; VOUT now reads back the PSU set-point
#
# Modeling level: HLE at amux_sweep() (the analog acquisition seam). The real
# amux_sweep busy-polls unmodeled ADC MMIO (0x4004c000, in the logger
# catch-all) and was SkipFunc'd in the stock config, leaving the global mV
# results struct (@0x20039510) all-zero -> "No voltage detected on VOUT/VREF".
# The AdcPsuModel handler publishes MODELED millivolt rails into that struct
# (VOUT/VREF = 3.3 V, an IO ramp IO0=0.40V..IO7=3.20V) so the firmware's own
# voltage rendering reports real values and the preflight passes naturally.
# psu_enable captures the `W` set-point so VOUT reads it back. See
# bp_handlers/bpv5/adcpsu.py + bpv5_config.yaml.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). Override with
# HAL_EMULATOR. The device is launched FIRST (the unicorn slow-joiner race).
#
# NOTE: uses DEDICATED ZMQ ports (5805/5806) and a uniquely-named runner so
# concurrent sibling bring-up agents (5815/5825) are never disturbed. Teardown
# is scoped to our own PIDs and the uniquely-named runner only (never a broad
# pkill).

set +e
set +m

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

VENV_BIN="${BPV5_VENV_BIN:-$REPO_ROOT/virtualenvs/halucinator/bin}"
[[ -x "$VENV_BIN/halucinator" ]] && export PATH="$VENV_BIN:$PATH"

if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi
TIMEOUT="${BPV5_ADCPSU_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ports + unique runner name (don't collide with sibling agents).
H_RX="${BPV5_ADCPSU_H_RX:-5805}"   # halucinator rx (terminal tx)
H_TX="${BPV5_ADCPSU_H_TX:-5806}"   # halucinator tx (terminal rx)
RUN_NAME="bpv5_adcpsu_run"

# From HiZ> measure all pins, enable the 3.3 V supply, measure again.
#   'v'        -> all-pin voltage table (modeled IO ramp + VOUT/VREF = 3.3 V)
#   'W 3.3 0'  -> enable PSU @ 3.3 V, current limit 0 (no fuse). Supplying both
#                the voltage AND current positional args skips the interactive
#                ui_prompt_float prompts (which would block the script). The
#                handler then calls psu_enable(3.3) -> our model captures the
#                set-point -> the firmware prints "Vreg output: 3.3V ...".
#   'v'        -> re-measure; VOUT reads back the PSU set-point (still 3.3 V)
ADCPSU_SCRIPT='v\rW 3.3 0\rv\r'
# The firmware prints "Vreg output: X.XV, Vref/Vout pin: X.XV" right after the
# PSU is enabled — a reliable post-`W` completion marker.
EXIT_MARKER="${BPV5_ADCPSU_EXIT_MARKER:-Vreg output}"
SCRIPT_DELAY="${BPV5_ADCPSU_SCRIPT_DELAY:-6}"

# --- cleanup (scope ONLY to our uniquely-named runner) -------------------
pkill -9 -f "$RUN_NAME"        2>/dev/null || true
sleep 1
rm -f bpv5_adcpsu_hal.log bpv5_adcpsu_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_ADCPSU_ATTEMPTS:-3}"
attempt=0
DEV_RC=1
while :; do
    attempt=$((attempt + 1))

    # --- device first (slow-joiner) --------------------------------------
    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (ADC/PSU measure, ports $H_TX/$H_RX) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            -r "$H_TX" -t "$H_RX" \
            --script "$ADCPSU_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_adcpsu_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    # --- halucinator -----------------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    HAL_EMULATOR="$EMULATOR" halucinator --emulator "$EMULATOR" \
            -r "$H_RX" -t "$H_TX" \
            -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
            -n "$RUN_NAME" >bpv5_adcpsu_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires:
    #  (a) the model trace shows amux_sweep published the modeled rails (VOUT
    #      3300 mV) and psu_enable captured the `W` set-point;
    #  (b) the firmware-rendered `v` table shows the modeled VOUT 3.3 V and the
    #      modeled IO pin ramp (a non-zero, non-3.3 pin proves it isn't a fluke);
    #  (c) the `W` summary reports the supply Enabled with the modeled rail
    #      ("Vref/Vout pin: 3.0V") — the firmware reading our PSU model back;
    #  (d) NO "No voltage detected" warning anywhere in the firmware output.
    MODEL_SWEEP=$(grep -c "amux_sweep -> VOUT=3300 mV" bpv5_adcpsu_hal.log)
    MODEL_PSU=$(grep -c "psu_enable(set=" bpv5_adcpsu_hal.log)
    # ANSI-strip the firmware CLI output.
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_adcpsu_dev.log)
    # The `v` table renders VOUT as "3.3" and an IO ramp (IO0=0.4 .. IO7=3.2).
    FW_VOUT=$(echo "$DEV_CLEAN" | grep -cE "3\.3V")
    # A modeled, distinctly-non-rail IO pin (e.g. IO3=1.6 / IO4=2.0 / IO5=2.4).
    FW_RAMP_PIN=$(echo "$DEV_CLEAN" | grep -cE "(1\.6|2\.0|2\.4)V")
    # The `W` summary line: supply enabled + the modeled Vout pin read back.
    FW_PSU_ENABLED=$(echo "$DEV_CLEAN" | grep -ciE "Enabled|Vref/Vout pin:")
    NO_VOLTAGE=$(echo "$DEV_CLEAN" | grep -ci "No voltage detected")

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_adcpsu_dev.log \
            && [[ "$MODEL_SWEEP" -ge 1 ]] \
            && [[ "$MODEL_PSU" -ge 1 ]] \
            && [[ "$FW_VOUT" -ge 1 ]] \
            && [[ "$FW_RAMP_PIN" -ge 1 ]] \
            && [[ "$FW_PSU_ENABLED" -ge 1 ]] \
            && [[ "$NO_VOLTAGE" -eq 0 ]]; then
        echo "=== bpv5 ADC/PSU test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware-rendered 'v' voltage table + 'W' PSU summary ---"
        echo "$DEV_CLEAN" | grep -aiE "Vout|VREF|IO[0-9]|[0-9]\.[0-9]V|GND|Vreg|supply|Current|mA" | head -30
        echo "--- modeled analog trace (model side) ---"
        grep -aE "AdcPsuModel\]" bpv5_adcpsu_hal.log | grep -av attached | head -10
        echo "--- 'No voltage detected' warnings: $NO_VOLTAGE (want 0) ---"
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no analog readout (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 ADC/PSU test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- firmware CLI output (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_adcpsu_dev.log | grep -aiE "VOUT|VREF|IO[0-9]|volt|No voltage|HiZ>" | tail -40 || true
echo "--- AdcPsuModel lines in bpv5_adcpsu_hal.log ---"
grep -aE "AdcPsuModel" bpv5_adcpsu_hal.log | grep -av Registering | tail -30 || true
echo "--- last 30 lines of bpv5_adcpsu_hal.log ---"
grep -av "Got message" bpv5_adcpsu_hal.log | tail -30 || true
exit 1
