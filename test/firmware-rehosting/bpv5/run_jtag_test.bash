#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# JTAG end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter JTAG mode through the CLI, run the firmware's blueTag IDCODE pinout
# scan against the *modeled* JTAG scan-chain target, and assert the firmware
# prints back the real ARM Cortex-M generic TAP IDCODE (0x4BA00477).
#
# This exercises the full path:
#   CLI 'bluetag jtag -c 6'  ->  bluetag_handler -> jtagScan
#     ->  bypassTest (pinout accepted) / detectDevices (1 TAP)
#     ->  getDeviceIDs writes IDCODE into ctx+0x3c
#     ->  displayDeviceDetails renders "[ Device 0 ]  0x4BA00477" + ARM decode.
#
# Unlike SPI (a clean per-byte leaf helper), JTAG/SWD is bit-banged against
# the RP2040 SIO GPIO registers with no per-bit software helper to hook, so
# the model lives at the device-discovery seam (see bp_handlers/bpv5/jtag.py;
# it falls back to the lowest clean routine that yields the scanned word).
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). Override with
# HAL_EMULATOR. The device is launched FIRST (the unicorn slow-joiner race).
#
# NOTE: uses dedicated ZMQ ports (5575/5576) and a uniquely-named runner so
# concurrent sibling bring-up agents on the default 5555/5556 ports are not
# disturbed. Halucinator binds rx=H_RX/tx=H_TX; the terminal must cross them
# (terminal rx=H_TX, terminal tx=H_RX).

set +e
set +m

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi
TIMEOUT="${BPV5_JTAG_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ports + unique runner name (don't collide with sibling agents).
H_RX="${BPV5_JTAG_H_RX:-5575}"   # halucinator rx (terminal tx)
H_TX="${BPV5_JTAG_H_TX:-5576}"   # halucinator tx (terminal rx)
RUN_NAME="bpv5_jtag_run"

# Mode-entry + scan. 'm','12' select JTAG (menu 12 — verified live). Then the
# blueTag IDCODE pinout scan over 6 channels.
JTAG_SCRIPT='m\r12\rbluetag jtag -c 6\r'
# Exit when the scanned device line is rendered ("[ Device 0 ]  0x4BA00477").
EXIT_MARKER="${BPV5_JTAG_EXIT_MARKER:-Device 0}"

# --- cleanup (scope ONLY to our uniquely-named runner) -------------------
pkill -9 -f "$RUN_NAME"        2>/dev/null || true
sleep 1
rm -f bpv5_jtag_hal.log bpv5_jtag_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

# --- device first (slow-joiner) ------------------------------------------
echo "=== Launching bpv5_terminal (JTAG IDCODE scan) ==="
python3 -m halucinator.external_devices.bpv5_terminal \
        -r "$H_TX" -t "$H_RX" \
        --script "$JTAG_SCRIPT" \
        --script-delay 6 \
        --exit-on "$EXIT_MARKER" \
        --max-runtime "$TIMEOUT" \
        >bpv5_jtag_dev.log 2>&1 &
DEV_PID=$!
sleep 4

# --- halucinator ---------------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) ==="
HAL_EMULATOR="$EMULATOR" halucinator --emulator "$EMULATOR" \
        -r "$H_RX" -t "$H_TX" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n "$RUN_NAME" >bpv5_jtag_hal.log 2>&1 &
HAL_PID=$!

if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi

# --- evaluate ------------------------------------------------------------
# PASS requires the modeled scan chain to have answered (model log) AND the
# firmware to have displayed the IDCODE on the device terminal.
MODEL_OK=$(grep -c "IDCODE -> 0x4BA00477" bpv5_jtag_hal.log)
# Strip ANSI from the firmware's device line and require the IDCODE.
RX_CLEAN=$(grep -a "Device 0" bpv5_jtag_dev.log | head -1 \
    | sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g')
if [[ "$DEV_RC" -eq 0 ]] \
        && grep -q "exit marker .* seen" bpv5_jtag_dev.log \
        && [[ "$MODEL_OK" -ge 1 ]] \
        && [[ "$RX_CLEAN" == *"0x4BA00477"* ]]; then
    echo "=== bpv5 JTAG test PASSED (--emulator $EMULATOR) ==="
    echo "--- firmware-rendered scan output ---"
    sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_jtag_dev.log \
        | grep -aE "Pinout|Device 0|mfg:" | head -5
    echo "--- modeled JTAG scan-chain trace (model side) ---"
    grep -aE "JtagTarget\].*(bypassTest|detectDevices|IDCODE)" bpv5_jtag_hal.log
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 JTAG test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 30 lines of bpv5_jtag_dev.log (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_jtag_dev.log | grep -av "Progress:" | tail -30 || true
echo "--- JtagTarget lines in bpv5_jtag_hal.log ---"
grep -aE "JtagTarget" bpv5_jtag_hal.log | grep -av Registering | tail -20 || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
exit 1
