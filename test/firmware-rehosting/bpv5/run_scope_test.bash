#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Scope (#14) + BINLOOP (#13) end-to-end test: boot the Bus Pirate v5 firmware
# under HALucinator and exercise the last two menu modes through the live CLI.
#
# Scope (#14) is the oscilloscope DISPLAY mode (display-mode index 1, entered
# with the `d 2` command — see bp_handlers/bpv5/scope.py for the RE). Entering it
# runs scope_setup (allocs the sample buffers) + scope_setup_exc and the
# firmware prints "Display mode: Scope" on the console. The ScopeModel handler
# injects a modeled DC waveform (raw 0x800 -> ~3.3 V) and neuters the raw
# ST7789 power-on (lcd_enable) that would fault headless.
#
# Backend: unicorn on macOS. Device launched FIRST (unicorn slow-joiner race).
# DEDICATED ZMQ ports (5835/5836) + uniquely-named runner + PID-scoped teardown
# so this never collides with or kills sibling agents' runs (NO broad pkill).

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
TIMEOUT="${BPV5_SCOPE_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ports + unique runner name (don't collide with sibling agents).
H_RX="${BPV5_SCOPE_H_RX:-5835}"   # halucinator rx (terminal tx)
H_TX="${BPV5_SCOPE_H_TX:-5836}"   # halucinator tx (terminal rx)
RUN_NAME="bpv5_scope_run"

# From HiZ> enter the Scope display mode with `d 2` (index 1 = Scope). The
# firmware echoes "Display mode: Scope". 's' starts a scope capture frame.
SCOPE_SCRIPT='d 2\r'
EXIT_MARKER="${BPV5_SCOPE_EXIT_MARKER:- Scope}"
SCRIPT_DELAY="${BPV5_SCOPE_SCRIPT_DELAY:-6}"

# --- cleanup (scope ONLY to our uniquely-named runner) -------------------
pkill -9 -f "$RUN_NAME" 2>/dev/null || true
sleep 1
rm -f bpv5_scope_hal.log bpv5_scope_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_SCOPE_ATTEMPTS:-3}"
attempt=0
DEV_RC=1
while :; do
    attempt=$((attempt + 1))

    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (Scope d 2, ports $H_TX/$H_RX) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            -r "$H_TX" -t "$H_RX" \
            --script "$SCOPE_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_scope_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    HAL_EMULATOR="$EMULATOR" halucinator --emulator "$EMULATOR" \
            -r "$H_RX" -t "$H_TX" \
            -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
            -n "$RUN_NAME" >bpv5_scope_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    # The device exits the instant it sees "Display: Scope" (printed by
    # scope_setup_exc), but scope_periodic — which injects the modeled
    # waveform into the firmware's sample buffer — fires on the NEXT core0
    # loop iterations. On fast backends (unicorn) it lands before the device
    # disconnects; on slower ones (avatar2/qemu/renode) it lands a few seconds
    # later. Give halucinator a bounded grace window to emit the sample-buffer
    # marker before teardown so the assert doesn't race the firmware loop.
    for _ in $(seq 1 "${BPV5_SCOPE_WAVE_WAIT:-25}"); do
        grep -q "scope sample buffer @" bpv5_scope_hal.log 2>/dev/null && break
        sleep 1
    done
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # PASS requires:
    #  (a) the model trace shows ScopeModel entered Scope mode;
    #  (b) the firmware-rendered console shows "Display mode: Scope".
    MODEL_ENTER=$(grep -c "scope_setup_exc — entering Scope" bpv5_scope_hal.log)
    MODEL_WAVE=$(grep -c "scope sample buffer @" bpv5_scope_hal.log)
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_scope_dev.log)
    FW_SCOPE=$(echo "$DEV_CLEAN" | grep -ciE "Display: *Scope")

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_scope_dev.log \
            && [[ "$MODEL_ENTER" -ge 1 ]] \
            && [[ "$MODEL_WAVE" -ge 1 ]] \
            && [[ "$FW_SCOPE" -ge 1 ]]; then
        echo "=== bpv5 Scope test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware-rendered Scope mode entry ---"
        echo "$DEV_CLEAN" | grep -aiE "Display: *Scope" | head -5
        echo "--- modeled scope trace (model side) ---"
        grep -aE "ScopeModel\]" bpv5_scope_hal.log | grep -av attached | head -10
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no Scope entry (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 Scope test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- firmware CLI output (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_scope_dev.log | tail -40 || true
echo "--- ScopeModel lines ---"
grep -aE "ScopeModel" bpv5_scope_hal.log | tail -20 || true
echo "--- last 40 lines of bpv5_scope_hal.log ---"
grep -av "Got message" bpv5_scope_hal.log | tail -40 || true
exit 1
