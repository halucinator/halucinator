#!/usr/bin/env bash
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
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

EMULATOR="${HAL_EMULATOR:-avatar2}"
TIMEOUT="${BPV5_SMOKE_TIMEOUT:-300}"
EXIT_MARKER="${BPV5_SMOKE_EXIT_MARKER:-work with pins}"

# --- cleanup -------------------------------------------------------------
pkill -9 -f qemu-system-arm   2>/dev/null || true
pkill -9 -f halucinator       2>/dev/null || true
pkill -9 -f bpv5_terminal     2>/dev/null || true
pkill -9 -f gdb-multiarch     2>/dev/null || true
sleep 1
rm -f bpv5_hal.log bpv5_dev.log

# --- launch halucinator --------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) ==="
HAL_EMULATOR="$EMULATOR" "$SCRIPT_DIR/run.sh" >bpv5_hal.log 2>&1 &
HAL_PID=$!

# Give halucinator a head start so its ZMQ sockets are bound by the time
# the device subscribes. The device sends its --prelude on first received
# tx_buf so it's safe even if this is short, but a couple of seconds keeps
# the logs tidier.
sleep 2

# --- launch the device in scripted mode ----------------------------------
# Rely on the device's own --max-runtime — no external `timeout` (the
# coreutils binary isn't present by default on macOS).
echo "=== Launching bpv5_terminal in scripted mode ==="
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
if python3 -m test.bpv5.bpv5_terminal \
        --script 'h\r\n' \
        --script-delay 2 \
        --exit-on "${EXIT_MARKER}" \
        --max-runtime "${TIMEOUT}" \
        >bpv5_dev.log 2>&1; then
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
