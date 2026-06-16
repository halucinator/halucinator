#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Smoke test: boot the Bus Pirate v5 firmware under HALucinator, drive it
# through the VT100 detection handshake using the bpv5_terminal external
# device, and verify the firmware reaches the HiZ> command shell and
# executes a help command.
#
# Mirrors the structure of test/STM32/example/run_test.bash:
#   1. Clean up any leftover halucinator / qemu processes.
#   2. Launch halucinator (via run.sh) in the background.
#   3. Launch bpv5_terminal in scripted mode, with --exit-on watching
#      for an unmistakable post-prompt marker.
#   4. PASS if the device exits cleanly with the marker observed.
#
# Override the backend with HAL_EMULATOR=unicorn (or qemu, ghidra, renode).
# Default is avatar2 — matching halucinator's default and the bpv5 demo's
# Docker-image expectation.
#
# Expected pass time: ~60s on unicorn, ~120s on avatar2+qemu.
# Hard timeout: 300s.

set -e
set +m  # disable job-control notifications ("Terminated: 15" on cleanup)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Default backend: explicit HAL_EMULATOR wins; else unicorn on macOS
# (qemu/avatar2 ship Linux binaries), avatar2 elsewhere.
if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi
TIMEOUT="${BPV5_SMOKE_TIMEOUT:-${BPV5_TIMEOUT:-300}}"
EXIT_MARKER="${BPV5_SMOKE_EXIT_MARKER:-work with pins}"

# --- cleanup -------------------------------------------------------------
pkill -9 -f qemu-system-arm   2>/dev/null || true
pkill -9 -f halucinator       2>/dev/null || true
pkill -9 -f bpv5_terminal     2>/dev/null || true
pkill -9 -f gdb-multiarch     2>/dev/null || true
sleep 1
rm -f bpv5_hal.log bpv5_dev.log

# --- launch the device FIRST --------------------------------------------
# Ordering matters on the in-process unicorn backend: it boots to the
# banner in well under a second, so if halucinator publishes the banner
# before the device's ZMQ SUB socket is connected, those bytes are lost
# to the PUB/SUB slow-joiner window and the device never sees a first
# tx_buf (so it never sends its --prelude). Starting the device first and
# giving its SUB a moment to bind closes that race. (qemu/avatar2 boot
# slowly enough that the original order also worked.)
echo "=== Launching bpv5_terminal in scripted mode ==="
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
python3 -m halucinator.external_devices.bpv5_terminal \
        --script 'h\r\n' \
        --script-delay 3 \
        --exit-on "${EXIT_MARKER}" \
        --max-runtime "${TIMEOUT}" \
        >bpv5_dev.log 2>&1 &
DEV_PID=$!

# Let the device's SUB socket connect before halucinator publishes.
sleep 3

# --- launch halucinator --------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) ==="
HAL_EMULATOR="$EMULATOR" "$SCRIPT_DIR/run.sh" >bpv5_hal.log 2>&1 &
HAL_PID=$!

# --- wait for the device to finish (marker seen or --max-runtime) --------
if wait "$DEV_PID"; then
    DEV_RC=0
else
    DEV_RC=$?
fi

# --- evaluate ------------------------------------------------------------
if [[ "$DEV_RC" -eq 0 ]] && grep -q "exit marker .* seen" bpv5_dev.log; then
    echo "=== bpv5 smoke test PASSED (--emulator $EMULATOR) ==="
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 smoke test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 30 lines of bpv5_dev.log ---"
tail -30 bpv5_dev.log || true
echo "--- last 50 lines of bpv5_hal.log ---"
tail -50 bpv5_hal.log || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
exit 1
