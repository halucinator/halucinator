#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# UART NMEA/GPS stretch test: boot the Bus Pirate v5 firmware under HALucinator,
# enter HW-UART mode, run the global 'gps' command, and assert the firmware's
# minmea decoder reports a position fix from a *modeled* NMEA GPS source.
#
# Path:
#   CLI 'm 3 ...defaults... gps'
#   -> nmea_decode_handler reads raw PL011 DR/FR @ 0x40034000
#   -> Rp2040Uart1Source peripheral streams "$GPGGA,...*59\r\n"
#   -> process_gps / minmea_parse_gga -> "$xxGGA: fix quality: 1".
#
# Uses NMEA-specific configs (bpv5_config_uart_nmea.yaml + the UART1 peripheral
# in bpv5_memory_uart_nmea.yaml). Backend: unicorn on macOS.
#
# Process hygiene: only kills the halucinator PID it launched (HAL_PID); never
# broad-pkills 'halucinator', so sibling agents are untouched.

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
TIMEOUT="${BPV5_UART_NMEA_TIMEOUT:-${BPV5_TIMEOUT:-110}}"

# Enter UART mode (m, 3), accept the 6 default setup prompts, then run 'gps'.
UART_SCRIPT="${BPV5_UART_NMEA_SCRIPT:-m\r3\r\r\r\r\r\r\rgps\r}"
# process_gps prints "fix quality" for a decoded GGA fix — the success marker.
EXIT_MARKER="${BPV5_UART_NMEA_EXIT_MARKER:-fix quality}"

# Unique ports so this can run alongside other bpv5 demos.
RX=5565; TX=5566
DEV_LOG="$REPO_ROOT/bpv5_uart_nmea_dev.log"
HAL_LOG="$REPO_ROOT/bpv5_uart_nmea_hal.log"

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f bpv5_uart_nmea_terminal 2>/dev/null || true
sleep 1
rm -f "$DEV_LOG" "$HAL_LOG"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

echo "=== Launching bpv5_terminal (UART gps / NMEA decode) ==="
python3 -m halucinator.external_devices.bpv5_terminal -r "$TX" -t "$RX" \
        --script "$UART_SCRIPT" \
        --script-delay 5 \
        --exit-on "$EXIT_MARKER" \
        --max-runtime "$TIMEOUT" \
        >"$DEV_LOG" 2>&1 &
DEV_PID=$!
sleep 3

echo "=== Launching halucinator (--emulator $EMULATOR, NMEA configs) ==="
halucinator --emulator "$EMULATOR" -r "$RX" -t "$TX" \
    -c test/firmware-rehosting/bpv5/bpv5_memory_uart_nmea.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_config_uart_nmea.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
    -n bpv5_uart_nmea >"$HAL_LOG" 2>&1 &
HAL_PID=$!

if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi

# PASS requires BOTH:
#  (1) the model byte trace: the UART1 source streamed sentence bytes (DR reads)
#  (2) the firmware-rendered fix line "$xxGGA: fix quality: 1".
DR_OK=$(grep -ac "Rp2040Uart1Source\] DR read" "$HAL_LOG")
FIX_CLEAN=$(grep -a "fix quality" "$DEV_LOG" | head -1 \
    | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')

if [[ "$DEV_RC" -eq 0 ]] \
        && grep -q "exit marker .* seen" "$DEV_LOG" \
        && [[ "$DR_OK" -ge 1 ]] \
        && [[ "$FIX_CLEAN" == *"fix quality"* ]]; then
    echo "=== bpv5 UART NMEA test PASSED (--emulator $EMULATOR) ==="
    echo "--- firmware GGA fix line ---"
    echo "$FIX_CLEAN"
    echo "--- modeled UART1 source: first/last streamed bytes ---"
    grep -aE "Rp2040Uart1Source\]" "$HAL_LOG" | head -4
    grep -aE "Rp2040Uart1Source\] DR read" "$HAL_LOG" | tail -3
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 UART NMEA test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 40 lines of bpv5_uart_nmea_dev.log (ansi-stripped) ---"
sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g' "$DEV_LOG" | tail -40 || true
echo "--- Rp2040Uart1Source / arm lines in hal log ---"
grep -aE "Rp2040Uart1Source|arm" "$HAL_LOG" | tail -20 || true
echo "--- last 20 lines of bpv5_uart_nmea_hal.log ---"
tail -20 "$HAL_LOG" || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
exit 1
