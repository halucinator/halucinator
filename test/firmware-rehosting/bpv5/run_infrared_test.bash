#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# INFRARED end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter INFRARED mode (#11), select the NEC protocol, and drive a real IR
# transaction in BOTH directions against the *modeled* NEC IR device:
#
#   RX: while sitting at the INFRARED-(NEC)> prompt, the firmware's
#       infrared_periodic loop calls nec_get_frame; our InfraredNecTarget seeds
#       the (faked) PIO RX FIFO with a modeled NEC frame so the firmware's OWN
#       decode+print renders:
#           (0xf708fb04) Address: 4 (0x04) Command: 8 (0x08)
#
#   TX: the CLI transaction '[0x0804]' writes the 16-bit value 0x0804 (byte0 =
#       NEC address 0x04, byte1 = NEC command 0x08) -> infrared_write ->
#       nec_write; our handler captures the emitted NEC wire frame and the
#       firmware renders:
#           TX: 0x0804.16
#
# Full paths exercised:
#   RX:  infrared_periodic -> nec_get_frame  (HLE seed -> real decode/print)
#   TX:  '[0x0804]' -> syntax_run_write -> infrared_write -> nec_write (HLE)
# Modeled device: NEC address=0x04, command=0x08 (wire frame 0xF708FB04 =
# addr,~addr,cmd,~cmd = 0x04 0xFB 0x08 0xF7).
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only), avatar2 elsewhere.
# Override with HAL_EMULATOR. The device is launched FIRST (the unicorn
# slow-joiner race — see run_tests.bash). DEDICATED ZMQ ports 5775/5776 +
# PID-scoped teardown so this never collides with or kills sibling agents' runs.

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
TIMEOUT="${BPV5_IR_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# DEDICATED ZMQ ports for the INFRARED agent (cross-wired: device tx == hal rx).
# 5775/5776 are reserved for this agent so it never collides with siblings.
HAL_RX_PORT="${BPV5_IR_HAL_RX:-5775}"
HAL_TX_PORT="${BPV5_IR_HAL_TX:-5776}"

# Mode-entry + NEC selection + TX transaction. After the menu number, IR has:
#   '2\r'  -> Protocol = NEC
#   '\r'   -> RX sensor = default (36-40kHz demodulator)
# Entering NEC mode arms the periodic RX loop (the modeled frame is decoded
# once on entry). Then the TX transaction:
#   '[0x0804]\r' -> '[' START (no-op for IR), write 0x0804 (addr 0x04 / cmd
#                   0x08), ']' STOP -> infrared_write -> nec_write.
# (A bare value alone does NOT compile to a write in IR mode — it must be
# framed by '[' ']', same as SPI/I2C transactions.)
IR_SCRIPT='m\r11\r2\r\r[0x0804]\r'
# After a successful NEC transmit the firmware renders the value line
# "TX: 0x0804.16" (the ".16" suffix is the 16-bit transmit count). That ".16"
# is a distinctive post-transaction marker that only appears once the full TX
# value has been rendered — unlike a bare "TX:" which also matches the
# "NEC TX modulation" setup banner and would exit before the transaction.
EXIT_MARKER="${BPV5_IR_EXIT_MARKER:-.16}"
SCRIPT_DELAY="${BPV5_IR_SCRIPT_DELAY:-9}"

# --- cleanup -------------------------------------------------------------
# Do NOT broad-pkill halucinator/qemu/bpv5_terminal — sibling agents share
# this host. We only ever kill the PIDs we spawn (DEV_PID/HAL_PID).
rm -f bpv5_ir_hal.log bpv5_ir_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

# Unicorn occasionally loses the ZMQ slow-joiner race and the firmware never
# receives the scripted keystrokes. That's a launch-timing flake, not a model
# bug — so retry the whole launch a few times before declaring failure.
ATTEMPTS="${BPV5_IR_ATTEMPTS:-3}"
attempt=0
while :; do
    attempt=$((attempt + 1))

    # --- device first (slow-joiner) --------------------------------------
    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (INFRARED NEC TX+RX, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$IR_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_ir_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    # --- halucinator -----------------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >bpv5_ir_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires BOTH directions to land, each proven by the model trace AND
    # the firmware-rendered CLI line:
    #   TX: model "[InfraredNecTarget] TX NEC frame: address=0x04 command=0x08"
    #       + firmware "TX: 0x0804" line.
    #   RX: model "[InfraredNecTarget] RX NEC frame seeded"
    #       + firmware-decoded "Address: 4 (0x04) Command: 8 (0x08)" line.
    MODEL_TX=$(grep -ac "InfraredNecTarget\] TX NEC frame: address=0x04 command=0x08" bpv5_ir_hal.log)
    MODEL_RX=$(grep -ac "InfraredNecTarget\] RX NEC frame seeded" bpv5_ir_hal.log)
    # Strip ANSI from the firmware lines.
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_ir_dev.log)
    TX_LINE=$(echo "$DEV_CLEAN" | grep -a "TX:" | head -1)
    RX_LINE=$(echo "$DEV_CLEAN" | grep -a "Address:" | head -1)

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_ir_dev.log \
            && [[ "$MODEL_TX" -ge 1 && "$MODEL_RX" -ge 1 ]] \
            && [[ "$TX_LINE" == *"0x08"*"04"* || "$TX_LINE" == *"0x0804"* ]] \
            && [[ "$RX_LINE" == *"0x04"* && "$RX_LINE" == *"0x08"* ]]; then
        echo "=== bpv5 INFRARED test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware RX-decoded line (NEC frame received) ---"
        echo "$RX_LINE"
        echo "--- firmware TX line (NEC frame transmitted) ---"
        echo "$TX_LINE"
        echo "--- modeled-IR-device byte/frame trace ---"
        grep -aE "InfraredNecTarget\] (TX|RX) NEC" bpv5_ir_hal.log
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no transaction (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 INFRARED test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 30 lines of bpv5_ir_dev.log (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_ir_dev.log | tail -30 || true
echo "--- modeled-IR-device trace (if any) ---"
grep -aE "InfraredNecTarget\]" bpv5_ir_hal.log || true
echo "--- last 40 lines of bpv5_ir_hal.log ---"
grep -av "Got message" bpv5_ir_hal.log | tail -40 || true
exit 1
