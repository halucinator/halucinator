#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# SPI end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter SPI mode through the CLI, run a JEDEC-ID read against the *modeled*
# SPI NOR flash target, and assert the firmware prints back the real ID
# bytes (EF 40 18 — a Winbond W25Q128).
#
# This exercises the full path:
#   CLI '[0x9f r:3]'  ->  spi_write/spi_read  ->  hwspi_write_read/hwspi_read
#   ->  SpiFlashTarget model  ->  RX: 0xEF 0x40 0x18 displayed on the terminal.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only), avatar2 elsewhere.
# Override with HAL_EMULATOR. The device is launched FIRST (see run_tests.bash
# for why — the unicorn slow-joiner race).

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
TIMEOUT="${BPV5_SPI_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Mode-entry + transaction. The five \r after '6' accept the default SPI
# prompts (speed/bits/polarity/phase/CS). Then a JEDEC RDID read.
SPI_SCRIPT='m\r6\r\r\r\r\r\r[0x9f r:3]\r'
# Exit when the transaction completes. The RX byte values carry inline
# ANSI colour escapes (0x<ESC>..18<ESC>), so we can't match "0x18"
# literally; "CS Disabled" is printed by spi_stop right after the read.
EXIT_MARKER="${BPV5_SPI_EXIT_MARKER:-CS Disabled}"

# --- cleanup -------------------------------------------------------------
pkill -9 -f qemu-system-arm   2>/dev/null || true
pkill -9 -f halucinator       2>/dev/null || true
pkill -9 -f bpv5_terminal     2>/dev/null || true
sleep 1
rm -f bpv5_spi_hal.log bpv5_spi_dev.log

# --- device first (slow-joiner) ------------------------------------------
echo "=== Launching bpv5_terminal (SPI JEDEC read) ==="
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
python3 -m halucinator.external_devices.bpv5_terminal \
        --script "$SPI_SCRIPT" \
        --script-delay 4 \
        --exit-on "$EXIT_MARKER" \
        --max-runtime "$TIMEOUT" \
        >bpv5_spi_dev.log 2>&1 &
DEV_PID=$!
sleep 3

# --- halucinator ---------------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) ==="
HAL_EMULATOR="$EMULATOR" "$SCRIPT_DIR/run.sh" >bpv5_spi_hal.log 2>&1 &
HAL_PID=$!

if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi

# --- evaluate ------------------------------------------------------------
# PASS requires the modeled flash to have answered with the full JEDEC ID
# AND the firmware to have displayed it on the RX line.
MODEL_OK=$(grep -c "MISO=0x18" bpv5_spi_hal.log)
# Strip ANSI from the firmware's RX line and require all three JEDEC bytes.
RX_CLEAN=$(grep -a "RX:" bpv5_spi_dev.log | head -1 \
    | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')
if [[ "$DEV_RC" -eq 0 ]] \
        && grep -q "exit marker .* seen" bpv5_spi_dev.log \
        && [[ "$MODEL_OK" -ge 1 ]] \
        && [[ "$RX_CLEAN" == *"0xEF"* && "$RX_CLEAN" == *"0x40"* \
              && "$RX_CLEAN" == *"0x18"* ]]; then
    echo "=== bpv5 SPI test PASSED (--emulator $EMULATOR) ==="
    echo "--- firmware RX line ---"
    grep -a "RX:" bpv5_spi_dev.log | head -1 \
        | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g'
    echo "--- modeled-flash byte exchange ---"
    grep -a "MOSI" bpv5_spi_hal.log
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 SPI test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 30 lines of bpv5_spi_dev.log ---"
tail -30 bpv5_spi_dev.log || true
echo "--- last 40 lines of bpv5_spi_hal.log ---"
tail -40 bpv5_spi_hal.log || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
exit 1
