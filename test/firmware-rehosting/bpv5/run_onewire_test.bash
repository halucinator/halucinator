#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# 1-WIRE end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter 1-WIRE mode through the CLI, run the firmware's DS18B20 conversion
# demo against the *modeled* DS18B20 temperature sensor, and assert the
# firmware reads back the modeled temperature (+25.062 C) and the 9-byte
# scratchpad with a valid Maxim CRC8.
#
# This exercises the full path:
#   CLI 'm 2'           ->  enter 1-WIRE mode (PIO bit-banged, no prompts)
#   CLI 'ds18b20'       ->  onewire_test_ds18b20_conversion demo, which drives
#                           onewire_reset / onewire_tx_byte / onewire_rx_byte
#   Ds18b20Target model ->  presence pulse, Skip-ROM(0xCC)/Convert-T(0x44)/
#                           Read-Scratchpad(0xBE), 9 scratchpad bytes
#   firmware            ->  "RX: 91 01 4b 46 7f ff 00 10 3d" + "Temperature: 25.062"
#
# The 1-WIRE leaf helpers are the HLE hook surface (1-WIRE is PIO bit-banged;
# there is no dedicated controller). See bp_handlers/bpv5/onewire.Ds18b20Target.
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
TIMEOUT="${BPV5_OW_TIMEOUT:-${BPV5_TIMEOUT:-120}}"

# Unique ZMQ ports so this test never collides with a sibling agent running
# another interface's bpv5_terminal (which defaults to 5555/5556). Halucinator
# rx == device tx and vice-versa.
OW_HAL_RX="${BPV5_OW_HAL_RX:-5575}"
OW_HAL_TX="${BPV5_OW_HAL_TX:-5576}"

# Mode-entry + demo. 'm\r2\r' opens the mode menu and selects 1-WIRE (menu
# #2). 1-WIRE mode has NO setup prompts (hw1wire_setup just returns 1), so we
# land straight at the '1-WIRE>' prompt. 'ds18b20\r' runs the conversion demo
# (Convert-T then Read-Scratchpad of the modeled sensor).
OW_SCRIPT='m\r2\rds18b20\r'
# Exit when the demo prints the decoded temperature. (The RX bytes carry no
# inline ANSI here; "Temperature:" is printed right after the 9-byte read.)
EXIT_MARKER="${BPV5_OW_EXIT_MARKER:-Temperature:}"

# --- cleanup: scope strictly to OUR unique ports -------------------------
# Our halucinator and terminal both carry our unique port numbers on their
# command lines, so these pkills can never hit a sibling agent's processes
# (which use the default 5555/5556).
pkill -9 -f "halucinator.* -r $OW_HAL_RX " 2>/dev/null || true
pkill -9 -f "bpv5_terminal.* --tx-port $OW_HAL_RX" 2>/dev/null || true
sleep 1
rm -f bpv5_onewire_hal.log bpv5_onewire_dev.log

# --- device first (slow-joiner) ------------------------------------------
echo "=== Launching bpv5_terminal (1-WIRE DS18B20 demo) on ports $OW_HAL_RX/$OW_HAL_TX ==="
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
python3 -m halucinator.external_devices.bpv5_terminal \
        --script "$OW_SCRIPT" \
        --script-delay 4 \
        --exit-on "$EXIT_MARKER" \
        --max-runtime "$TIMEOUT" \
        --tx-port "$OW_HAL_RX" \
        --rx-port "$OW_HAL_TX" \
        >bpv5_onewire_dev.log 2>&1 &
DEV_PID=$!
sleep 3

# --- halucinator ---------------------------------------------------------
echo "=== Launching halucinator (--emulator $EMULATOR) on ports $OW_HAL_RX/$OW_HAL_TX ==="
export PYTHONUNBUFFERED=1
# Launch halucinator directly (not run.sh) so we can pin unique ZMQ ports.
# The unique '-r $OW_HAL_RX' is what scopes our cleanup pkills.
halucinator --emulator "$EMULATOR" \
    -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
    -r "$OW_HAL_RX" -t "$OW_HAL_TX" \
    -n bpv5 >bpv5_onewire_hal.log 2>&1 &
HAL_PID=$!

if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi

# --- evaluate ------------------------------------------------------------
# PASS requires BOTH:
#  (1) the model drove the bus: presence pulse + Read-Scratchpad RX of the
#      scratchpad CRC byte 0x3D (proves the firmware clocked our bytes in).
#  (2) the firmware decoded + displayed the modeled temperature 25.062 C
#      (proves the 9-byte scratchpad passed the firmware's CRC check and the
#      temperature math).
MODEL_PRESENCE=$(grep -c "presence pulse" bpv5_onewire_hal.log)
MODEL_RX_CRC=$(grep -c "RX byte=0x3D" bpv5_onewire_hal.log)
# Strip ANSI from the firmware's RX/Temperature lines.
RX_CLEAN=$(grep -a "RX:" bpv5_onewire_dev.log | head -1 \
    | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')
TEMP_CLEAN=$(grep -a "Temperature:" bpv5_onewire_dev.log | head -1 \
    | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g')

if [[ "$DEV_RC" -eq 0 ]] \
        && grep -q "exit marker .* seen" bpv5_onewire_dev.log \
        && [[ "$MODEL_PRESENCE" -ge 1 ]] \
        && [[ "$MODEL_RX_CRC" -ge 1 ]] \
        && [[ "$TEMP_CLEAN" == *"25.06"* ]] \
        && ! grep -qa "CRC Fail" bpv5_onewire_dev.log; then
    echo "=== bpv5 1-WIRE test PASSED (--emulator $EMULATOR) ==="
    echo "--- firmware RX scratchpad line ---"
    echo "$RX_CLEAN"
    echo "--- firmware decoded temperature ---"
    echo "$TEMP_CLEAN"
    echo "--- modeled-DS18B20 byte exchange ---"
    grep -aE "BUS RESET|TX byte|RX byte" bpv5_onewire_hal.log
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
    exit 0
fi

echo "=== bpv5 1-WIRE test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "MODEL_PRESENCE=$MODEL_PRESENCE MODEL_RX_CRC=$MODEL_RX_CRC"
echo "RX_CLEAN=$RX_CLEAN"
echo "TEMP_CLEAN=$TEMP_CLEAN"
echo "--- last 40 lines of bpv5_onewire_dev.log ---"
tail -40 bpv5_onewire_dev.log || true
echo "--- last 50 lines of bpv5_onewire_hal.log ---"
tail -50 bpv5_onewire_hal.log || true
{ kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true
exit 1
