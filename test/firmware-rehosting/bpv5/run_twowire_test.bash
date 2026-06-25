#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# 2WIRE + 3WIRE end-to-end test: boot the Bus Pirate v5 firmware under
# HALucinator, enter each PIO-bit-banged mode through the CLI, run a real
# transaction against the *modeled* target device, and assert the firmware
# prints back the known bytes the model drove on the bus.
#
# 2WIRE (menu #7), modeled SLE4442-flavoured smartcard/memory:
#   CLI '[0x30 0x00 r:4]'
#     -> hw2wire_start / hw2wire_write / hw2wire_read / hw2wire_stop
#     -> pio_hw2wire_start / put16 / get16 / stop  (PIO leaf helpers)
#     -> TwoWireTarget: cmd 0x30 READ-MAIN, addr 0x00 -> ramp bytes 0x00..0x03
#     -> firmware renders the RX bytes  00 01 02 03  on the terminal.
#
# 3WIRE (menu #8), modeled 93Cxx Microwire EEPROM:
#   CLI '[0x80 r:4]'
#     -> hw3wire_start / hw3wire_write / hw3wire_read / hw3wire_stop
#     -> pio_hw3wire_get16 (full-duplex leaf helper)
#     -> ThreeWireTarget: cmd 0x80 arms a read -> signature 93 C4 6E 5A
#     -> firmware renders the RX bytes  93 C4 6E 5A  on the terminal.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). The device is
# launched FIRST (the unicorn slow-joiner race — see run_tests.bash). Unique
# ZMQ ports + PID-scoped teardown so this never collides with or kills sibling
# agents' runs.

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
TIMEOUT="${BPV5_2W_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ZMQ ports for this agent (cross-wired: device tx == hal rx).
HAL_RX_PORT="${BPV5_2W_HAL_RX:-5755}"
HAL_TX_PORT="${BPV5_2W_HAL_TX:-5756}"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_2W_ATTEMPTS:-3}"

# run_one <tag> <script> <exit-marker> <hal-log> <dev-log>
# Launches device-first then halucinator; returns device rc.
run_one() {
    local tag="$1" script="$2" marker="$3" hal_log="$4" dev_log="$5"
    local script_delay="${6:-9}"
    rm -f "$hal_log" "$dev_log"
    echo "=== Launching bpv5_terminal ($tag, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$script" \
            --script-delay "$script_delay" \
            --exit-on "$marker" \
            --max-runtime "$TIMEOUT" \
            >"$dev_log" 2>&1 &
    DEV_PID=$!
    # Let the device's SUB socket bind before halucinator joins (unicorn
    # slow-joiner race). 5s is comfortably above the observed flake window.
    sleep 5
    echo "=== Launching halucinator ($tag, --emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >"$hal_log" 2>&1 &
    HAL_PID=$!
    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    return "$DEV_RC"
}

# Strip ANSI from a device-log RX line.
rx_line() {
    grep -a "RX:" "$1" | head -1 | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g'
}

# --------------------------- 2WIRE -------------------------------------- #
# m\r7\r\r\r enters 2WIRE (menu #7) accepting the setup prompt defaults
# (mirror I2C's 3 \r). Then a smartcard memory read:
#   [0x30 0x00 r:4]  -> START, cmd 0x30 (READ-MAIN), addr 0x00, read 4, STOP.
# The modeled memory is a ramp, so the 4 bytes are 0x00 0x01 0x02 0x03.
TWO_SCRIPT='m\r7\r\r\r[0x30 0x00 r:4]\r'
# Post-transaction completion marker. The PIO modes do NOT render an
# "I2C STOP" label (that earlier guess was wrong — it never prints for
# 2WIRE/3WIRE, so the terminal timed out every run). The firmware echoes
# the read bytes as "RX: 0x00 0x01 0x02 0x03 "; note the bare "RX:" label
# is printed BEFORE the get16 reads run, so it is too early. The LAST
# streamed byte token "0x03 " (with trailing space) appears strictly AFTER
# all four modeled reads complete and ONLY in the RX render (the typed
# command "[0x30 0x00 r:4]" has no 0x03), so it is a precise, prompt
# success marker.
TWO_MARKER="${BPV5_2W_MARKER:-0x03 }"

# --------------------------- 3WIRE -------------------------------------- #
# m\r8\r\r\r enters 3WIRE (menu #8). Then a Microwire read:
#   [0x80 r:4]  -> CS, cmd 0x80 (arm read), read 4 bytes, CS off.
# The model streams its recognizable signature 0x93 0xC4 0x6E 0x5A first.
THREE_SCRIPT='m\r8\r\r\r[0x80 r:4]\r'
# Post-transaction marker, same principle as 2WIRE: the firmware renders
# "RX: 0x93 0xC4 0x6E 0x5A "; the LAST streamed byte token "0x5A " appears
# strictly AFTER all four modeled reads complete and only in the RX render
# (the typed command "[0x80 r:4]" has no 0x5A).
THREE_MARKER="${BPV5_3W_MARKER:-0x5A }"

overall=0

# ====================== run 2WIRE (with retries) ======================== #
TWO_OK=0
for ((a=1; a<=ATTEMPTS; a++)); do
    echo "########## 2WIRE attempt $a/$ATTEMPTS ##########"
    run_one "2WIRE" "$TWO_SCRIPT" "$TWO_MARKER" \
            bpv5_2wire_hal.log bpv5_2wire_dev.log 10
    DEV_RC=$?
    M_START=$(grep -c "TwoWireTarget\] START" bpv5_2wire_hal.log)
    M_M0=$(grep -c "TwoWireTarget\] MISO=0x00" bpv5_2wire_hal.log)
    M_M1=$(grep -c "TwoWireTarget\] MISO=0x01" bpv5_2wire_hal.log)
    M_M2=$(grep -c "TwoWireTarget\] MISO=0x02" bpv5_2wire_hal.log)
    M_M3=$(grep -c "TwoWireTarget\] MISO=0x03" bpv5_2wire_hal.log)
    RXC=$(rx_line bpv5_2wire_dev.log)
    if [[ "$M_START" -ge 1 && "$M_M0" -ge 1 && "$M_M1" -ge 1 \
          && "$M_M2" -ge 1 && "$M_M3" -ge 1 ]] \
       && grep -qiE "0?x?00.*0?x?01.*0?x?02.*0?x?03" <<<"$RXC"; then
        TWO_OK=1
        echo "=== 2WIRE PASSED (attempt $a) ==="
        echo "--- firmware RX line ---"; echo "$RXC"
        echo "--- modeled 2WIRE byte exchange ---"
        grep -aE "TwoWireTarget\]" bpv5_2wire_hal.log
        break
    fi
    echo "=== 2WIRE attempt $a no/partial transaction — retrying ==="
    sleep 2
done
[[ "$TWO_OK" -eq 1 ]] || { overall=1; echo "=== 2WIRE FAILED ==="; }

# ====================== run 3WIRE (with retries) ======================== #
THREE_OK=0
for ((a=1; a<=ATTEMPTS; a++)); do
    echo "########## 3WIRE attempt $a/$ATTEMPTS ##########"
    run_one "3WIRE" "$THREE_SCRIPT" "$THREE_MARKER" \
            bpv5_3wire_hal.log bpv5_3wire_dev.log 10
    DEV_RC=$?
    M_CS=$(grep -c "ThreeWireTarget\] CS asserted" bpv5_3wire_hal.log)
    M_S0=$(grep -c "ThreeWireTarget\] MISO=0x93" bpv5_3wire_hal.log)
    M_S1=$(grep -c "ThreeWireTarget\] MISO=0xC4" bpv5_3wire_hal.log)
    M_S2=$(grep -c "ThreeWireTarget\] MISO=0x6E" bpv5_3wire_hal.log)
    M_S3=$(grep -c "ThreeWireTarget\] MISO=0x5A" bpv5_3wire_hal.log)
    RXC=$(rx_line bpv5_3wire_dev.log)
    if [[ "$M_CS" -ge 1 && "$M_S0" -ge 1 && "$M_S1" -ge 1 \
          && "$M_S2" -ge 1 && "$M_S3" -ge 1 ]] \
       && grep -qiE "93.*c4.*6e.*5a" <<<"$RXC"; then
        THREE_OK=1
        echo "=== 3WIRE PASSED (attempt $a) ==="
        echo "--- firmware RX line ---"; echo "$RXC"
        echo "--- modeled 3WIRE byte exchange ---"
        grep -aE "ThreeWireTarget\]" bpv5_3wire_hal.log
        break
    fi
    echo "=== 3WIRE attempt $a no/partial transaction — retrying ==="
    sleep 2
done
[[ "$THREE_OK" -eq 1 ]] || { overall=1; echo "=== 3WIRE FAILED ==="; }

if [[ "$overall" -eq 0 ]]; then
    echo "=== bpv5 2WIRE+3WIRE test PASSED (--emulator $EMULATOR) ==="
else
    echo "=== bpv5 2WIRE+3WIRE test FAILED (--emulator $EMULATOR) ==="
    echo "--- 2wire dev tail ---";  tail -25 bpv5_2wire_dev.log 2>/dev/null
    echo "--- 2wire model lines ---"; grep -aE "TwoWireTarget\]" bpv5_2wire_hal.log 2>/dev/null | tail -20
    echo "--- 3wire dev tail ---";  tail -25 bpv5_3wire_dev.log 2>/dev/null
    echo "--- 3wire model lines ---"; grep -aE "ThreeWireTarget\]" bpv5_3wire_hal.log 2>/dev/null | tail -20
fi
exit "$overall"
