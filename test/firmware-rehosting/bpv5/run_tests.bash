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

set +m  # disable job-control notifications ("Terminated: 15" on cleanup)
# NB: no `set -e` — the retry loop below handles failures explicitly.

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
# Retry the whole run a few times: on avatar2/qemu the emulator can
# transiently fail to spin up (e.g. "GDBProtocol was unable to connect" when
# QEMU's gdbstub isn't listening yet). Those are startup races, not firmware
# failures — a fresh spawn succeeds. Genuine breakage still fails all attempts.
ATTEMPTS="${BPV5_SMOKE_ATTEMPTS:-3}"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."

_cleanup() {
    pkill -9 -f qemu-system-arm   2>/dev/null || true
    pkill -9 -f halucinator       2>/dev/null || true
    pkill -9 -f bpv5_terminal     2>/dev/null || true
    pkill -9 -f gdb-multiarch     2>/dev/null || true
}

# One attempt. Returns 0 = pass, 2 = halucinator died at startup (transient,
# worth retrying), 1 = device ran but the exit marker was not seen.
run_attempt() {
    _cleanup
    sleep 1
    rm -f bpv5_hal.log bpv5_dev.log

    # --- launch the device FIRST ----------------------------------------
    # Ordering matters on the in-process unicorn backend: it boots to the
    # banner in well under a second, so if halucinator publishes the banner
    # before the device's ZMQ SUB socket is connected, those bytes are lost
    # to the PUB/SUB slow-joiner window and the device never sees a first
    # tx_buf (so it never sends its --prelude). Starting the device first and
    # giving its SUB a moment to bind closes that race. (qemu/avatar2 boot
    # slowly enough that the original order also worked.)
    echo "=== Launching bpv5_terminal in scripted mode ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --script 'h\r\n' \
            --script-delay 3 \
            --exit-on "${EXIT_MARKER}" \
            --max-runtime "${TIMEOUT}" \
            >bpv5_dev.log 2>&1 &
    DEV_PID=$!

    # Let the device's SUB socket connect before halucinator publishes.
    sleep 3

    # --- launch halucinator ---------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    HAL_EMULATOR="$EMULATOR" "$SCRIPT_DIR/run.sh" >bpv5_hal.log 2>&1 &
    HAL_PID=$!

    # Wait for the device to finish, but bail out FAST if halucinator dies
    # first (a startup crash) instead of blocking on the device's full
    # --max-runtime. On unicorn/qemu halucinator runs until we kill it, so
    # the loop simply waits for the device.
    local DEV_RC
    while kill -0 "$DEV_PID" 2>/dev/null; do
        if ! kill -0 "$HAL_PID" 2>/dev/null; then
            echo "=== halucinator exited before the device finished (startup failure) ==="
            kill "$DEV_PID" 2>/dev/null || true
            wait "$DEV_PID" 2>/dev/null || true
            return 2
        fi
        sleep 1
    done
    wait "$DEV_PID"; DEV_RC=$?
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true

    if [[ "$DEV_RC" -eq 0 ]] && grep -q "exit marker .* seen" bpv5_dev.log; then
        return 0
    fi
    return 1
}

for attempt in $(seq 1 "$ATTEMPTS"); do
    echo "########## bpv5 smoke attempt $attempt/$ATTEMPTS (--emulator $EMULATOR) ##########"
    if run_attempt; then
        echo "=== bpv5 smoke test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        exit 0
    fi
    rc=$?
    echo "=== bpv5 smoke attempt $attempt failed (rc=$rc) ==="
    echo "--- last 30 lines of bpv5_dev.log ---"
    tail -30 bpv5_dev.log 2>/dev/null || true
    echo "--- last 50 lines of bpv5_hal.log ---"
    tail -50 bpv5_hal.log 2>/dev/null || true
done

echo "=== bpv5 smoke test FAILED after $ATTEMPTS attempts (--emulator $EMULATOR) ==="
_cleanup
exit 1
