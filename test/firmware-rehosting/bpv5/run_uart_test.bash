#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# UART end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter HW-UART mode through the CLI, run a write+read transaction against the
# *modeled* serial peer (a loopback/echo peer), and assert the firmware prints
# back the very byte it transmitted.
#
# This exercises the full path:
#   CLI '[0x41 r:1]' -> hwuart_write/hwuart_read (the leaf PL011 helpers)
#   -> UartPeerTarget loopback model -> RX: 0x41 displayed on the terminal.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). Override with
# HAL_EMULATOR. The device is launched FIRST (the unicorn slow-joiner race).
#
# Process hygiene: this runner is UART-specific and only kills the halucinator
# PID it launched itself (HAL_PID) plus qemu-system-arm (none on macOS). It
# does NOT broad-pkill 'halucinator', so sibling agents' runs are untouched.

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
TIMEOUT="${BPV5_UART_TIMEOUT:-${BPV5_TIMEOUT:-100}}"

# Mode-entry + transaction. 'm' opens the mode menu, '3' selects UART. The
# trailing \r accept the default UART setup prompts (baud / data bits / parity
# / stop bits / flow control / blocking). Then a loopback exchange: write 0x41
# ('A') and read 1 byte back.
UART_SCRIPT="${BPV5_UART_SCRIPT:-m\r3\r\r\r\r\r\r\r[0x41 r:1]\r}"
# The firmware prints "RX:" with the received byte after the read; the ']'
# that closes the transaction then prints "UART CLOSE". The RX byte carries
# inline ANSI colour, so we exit on the stable "UART CLOSE" marker that follows
# the read (mirrors the SPI test's "CS Disabled").
EXIT_MARKER="${BPV5_UART_EXIT_MARKER:-UART CLOSE}"

DEV_LOG="$REPO_ROOT/bpv5_uart_dev.log"
HAL_LOG="$REPO_ROOT/bpv5_uart_hal.log"

# --- cleanup (own PIDs only; never broad-pkill halucinator) --------------
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f bpv5_terminal   2>/dev/null || true
sleep 1
rm -f "$DEV_LOG" "$HAL_LOG"

# --- device first (slow-joiner) ------------------------------------------
echo "=== Launching bpv5_terminal (UART loopback write+read) ==="
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
python3 -m halucinator.external_devices.bpv5_terminal \
        --script "$UART_SCRIPT" \
        --script-delay 5 \
        --exit-on "$EXIT_MARKER" \
        --max-runtime "$TIMEOUT" \
        >"$DEV_LOG" 2>&1 &
DEV_PID=$!
sleep 3

# --- halucinator ---------------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) ==="
HAL_EMULATOR="$EMULATOR" "$SCRIPT_DIR/run.sh" >"$HAL_LOG" 2>&1 &
HAL_PID=$!

if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi

# --- evaluate ------------------------------------------------------------
# PASS requires BOTH:
#  (1) the model byte trace: firmware TX byte hit the peer AND the peer's RX
#      byte (the echoed 0x41) was returned;
#  (2) the firmware-rendered RX line showing the expected byte 0x41.
TX_OK=$(grep -ac "TX firmware->peer = 0x41" "$HAL_LOG")
RX_OK=$(grep -ac "RX peer->firmware = 0x41" "$HAL_LOG")
# Strip ANSI from the firmware's RX line and require the echoed byte.
RX_CLEAN=$(grep -a "RX:" "$DEV_LOG" | head -1 \
    | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')

if [[ "$DEV_RC" -eq 0 ]] \
        && grep -q "exit marker .* seen" "$DEV_LOG" \
        && [[ "$TX_OK" -ge 1 ]] && [[ "$RX_OK" -ge 1 ]] \
        && [[ "$RX_CLEAN" == *"0x41"* ]]; then
    echo "=== bpv5 UART test PASSED (--emulator $EMULATOR) ==="
    echo "--- firmware RX line ---"
    echo "$RX_CLEAN"
    echo "--- modeled-peer byte exchange ---"
    grep -aE "UartPeerTarget\] (TX|RX)" "$HAL_LOG"
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 UART test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 40 lines of bpv5_uart_dev.log ---"
sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g' "$DEV_LOG" | tail -40 || true
echo "--- UartPeerTarget lines in bpv5_uart_hal.log ---"
grep -aE "UartPeerTarget" "$HAL_LOG" | tail -30 || true
echo "--- last 20 lines of bpv5_uart_hal.log ---"
tail -20 "$HAL_LOG" || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
exit 1
