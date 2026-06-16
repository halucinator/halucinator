#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# I2C end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter I2C mode through the CLI, run a 24C02 EEPROM page read against the
# *modeled* I2C EEPROM target, and assert the firmware prints back the real
# EEPROM bytes the model drove on the bus.
#
# Full path exercised:
#   CLI '[0xA0 0x00 [0xA1 r:2]'
#     -> hwi2c_start / hwi2c_write / hwi2c_read / hwi2c_stop
#     -> pio_i2c_{start,restart,write,read,stop}_timeout (PIO leaf helpers)
#     -> I2cEepromTarget model (24C02 @ 7-bit 0x50, ramp content)
#     -> firmware renders  "RX: 0x00 ACK 0x01 NACK"  on the terminal.
# The 24C02 backing store is a ramp (byte n == n), so a 2-byte read from
# word-address 0x00 yields 0x00 0x01.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only), avatar2 elsewhere.
# Override with HAL_EMULATOR. The device is launched FIRST (the unicorn
# slow-joiner race — see run_tests.bash). Unique ZMQ ports + PID-scoped
# teardown so this never collides with or kills sibling agents' runs.

set +e
set +m

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Put the project venv on PATH so the `halucinator` entrypoint resolves even
# from a non-interactive shell (the venv python is the supported interpreter).
VENV_BIN="${BPV5_VENV_BIN:-$REPO_ROOT/virtualenvs/halucinator/bin}"
[[ -x "$VENV_BIN/halucinator" ]] && export PATH="$VENV_BIN:$PATH"

if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi
TIMEOUT="${BPV5_I2C_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Unique ZMQ ports (cross-wired: device tx == hal rx). Avoids colliding with
# the default 5555/5556 that sibling agents use.
HAL_RX_PORT="${BPV5_I2C_HAL_RX:-5755}"
HAL_TX_PORT="${BPV5_I2C_HAL_TX:-5756}"

# Mode-entry + transaction. After the menu number, I2C has exactly TWO setup
# prompts (speed, clock-stretch) — accept both defaults with '\r\r'. Then a
# 24Cxx page read:
#   [0xA0 0x00  -> START, control 0xA0 (addr 0x50 W), word-addr 0x00
#   [0xA1 r:2]  -> repeated-START, control 0xA1 (addr 0x50 R), read 2 bytes, STOP
I2C_SCRIPT='m\r5\r\r\r[0xA0 0x00 [0xA1 r:2]\r'
# The firmware prints "I2C STOP" right after rendering the RX bytes, so it is
# a reliable post-transaction completion marker in the device log.
EXIT_MARKER="${BPV5_I2C_EXIT_MARKER:-I2C STOP}"
SCRIPT_DELAY="${BPV5_I2C_SCRIPT_DELAY:-9}"

# --- cleanup -------------------------------------------------------------
# Do NOT broad-pkill halucinator/qemu/bpv5_terminal — sibling agents share
# this host. We only ever kill the PIDs we spawn (DEV_PID/HAL_PID).
rm -f bpv5_i2c_hal.log bpv5_i2c_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

# Unicorn occasionally loses the ZMQ slow-joiner race and the firmware never
# receives the scripted keystrokes (boot stalls before the USB-CDC console).
# That's a launch-timing flake, not a model bug — so retry the whole launch a
# few times before declaring failure.
ATTEMPTS="${BPV5_I2C_ATTEMPTS:-3}"
attempt=0
while :; do
    attempt=$((attempt + 1))

    # --- device first (slow-joiner) --------------------------------------
    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (I2C EEPROM read, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$I2C_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_i2c_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    # --- halucinator -----------------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >bpv5_i2c_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires BOTH:
    #  (a) the model byte-trace shows the firmware drove the bus and our 24C02
    #      ACKed at 0x50 and answered the read with the ramp bytes 0x00, 0x01;
    #  (b) the firmware-rendered RX line shows those bytes.
    MODEL_ACK=$(grep -c "addr=0x50 W) -> ACK" bpv5_i2c_hal.log)
    MODEL_MISO0=$(grep -c "MISO=0x00" bpv5_i2c_hal.log)
    MODEL_MISO1=$(grep -c "MISO=0x01" bpv5_i2c_hal.log)
    # Strip ANSI from the firmware's RX line and require both ramp bytes.
    RX_CLEAN=$(grep -a "RX:" bpv5_i2c_dev.log | head -1 \
        | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')
    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_i2c_dev.log \
            && [[ "$MODEL_ACK" -ge 1 ]] \
            && [[ "$MODEL_MISO0" -ge 1 && "$MODEL_MISO1" -ge 1 ]] \
            && [[ "$RX_CLEAN" == *"0x00"* && "$RX_CLEAN" == *"0x01"* ]]; then
        echo "=== bpv5 I2C test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware RX line ---"
        echo "$RX_CLEAN"
        echo "--- modeled-EEPROM byte exchange ---"
        grep -aE "I2cEepromTarget\]" bpv5_i2c_hal.log
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no transaction (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 I2C test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 30 lines of bpv5_i2c_dev.log ---"
tail -30 bpv5_i2c_dev.log || true
echo "--- modeled-EEPROM byte exchange (if any) ---"
grep -aE "I2cEepromTarget\]" bpv5_i2c_hal.log || true
echo "--- last 40 lines of bpv5_i2c_hal.log ---"
grep -av "Got message" bpv5_i2c_hal.log | tail -40 || true
exit 1
