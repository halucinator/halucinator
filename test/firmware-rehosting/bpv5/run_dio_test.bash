#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# DIO (raw per-pin digital I/O) end-to-end test: boot the Bus Pirate v5
# firmware under HALucinator, enter DIO mode (m menu #9) through the CLI, and
# exercise the *modeled* per-pin GPIO target:
#
#   '@ 5'  -> read BIO5  -> modeled externally-driven HIGH input   -> reads 1
#   'A 4'  -> drive BIO4 output HIGH
#   '@ 4'  -> read BIO4  -> reads back the driven HIGH              -> reads 1
#   'a 4'  -> drive BIO4 output LOW
#   '@ 4'  -> read BIO4  -> reads back the driven LOW               -> reads 0
#
# Full path exercised (verified live — all funnel through the bio leaves):
#   CLI 'A <IOx>' / 'a <IOx>' (output HIGH/LOW)
#                        -> bio_output(x) + bio_put(x, 1|0)  (model records)
#                        -> firmware renders 'IO<x> set to OUTPUT: 1|0'.
#   CLI '@ <IOx>'        (input/read)
#                        -> bio_input(x) + bio_get(x)        (model answers)
#                        -> firmware renders 'IO<x> set to INPUT: <level>'.
#
# DIO is raw GPIO over SIO (0xd0000000) + a 74-shift register; per the playbook
# we HLE at the bio leaf helpers (bio_get/bio_put/bio_output/bio_input) so the
# SIO atomic-alias register semantics never need emulating. See
# bp_handlers/bpv5/dio.py + bpv5_config.yaml.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). Override with
# HAL_EMULATOR. The device is launched FIRST (the unicorn slow-joiner race).
#
# NOTE: uses dedicated ZMQ ports (5785/5786) and a uniquely-named runner so
# concurrent sibling bring-up agents are not disturbed. Teardown is scoped to
# our own PIDs and the uniquely-named runner only (never a broad pkill).

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
TIMEOUT="${BPV5_DIO_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ports + unique runner name (don't collide with sibling agents).
H_RX="${BPV5_DIO_H_RX:-5785}"   # halucinator rx (terminal tx)
H_TX="${BPV5_DIO_H_TX:-5786}"   # halucinator tx (terminal rx)
RUN_NAME="bpv5_dio_run"

# Mode-entry + pin ops. 'm','9' select DIO (menu 9 — verified live). DIO setup
# has NO prompts (dio_setup/dio_setup_exc just return 1), so go straight to the
# per-pin commands: read the modeled HIGH input, then drive/read-back BIO4.
DIO_SCRIPT='m\r9\r@ 5\rA 4\r@ 4\ra 4\r@ 4\r'
# Exit when the FINAL read renders. The firmware wraps the rendered pin value
# in ANSI colour codes ("INPUT: \e[..m<v>\e[0m"), so the plain text "INPUT: 0"
# never appears in the raw stream. The exact colour-wrapped value-0 token is
# emitted exactly once — by the last command ('@ 4' after 'a 4', read-back
# LOW) — making it a unique, reliable post-sequence marker. ($'...' embeds the
# real ESC bytes; the terminal matches --exit-on against the raw byte stream.)
EXIT_MARKER=$'INPUT: \x1b[38;2;83;166;230m0\x1b[0m'
[[ -n "$BPV5_DIO_EXIT_MARKER" ]] && EXIT_MARKER="$BPV5_DIO_EXIT_MARKER"
SCRIPT_DELAY="${BPV5_DIO_SCRIPT_DELAY:-6}"

# --- cleanup (scope ONLY to our uniquely-named runner) -------------------
pkill -9 -f "$RUN_NAME"        2>/dev/null || true
sleep 1
rm -f bpv5_dio_hal.log bpv5_dio_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_DIO_ATTEMPTS:-3}"
attempt=0
DEV_RC=1
while :; do
    attempt=$((attempt + 1))

    # --- device first (slow-joiner) --------------------------------------
    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (DIO pin ops, ports $H_TX/$H_RX) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            -r "$H_TX" -t "$H_RX" \
            --script "$DIO_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_dio_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    # --- halucinator -----------------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    HAL_EMULATOR="$EMULATOR" halucinator --emulator "$EMULATOR" \
            -r "$H_RX" -t "$H_TX" \
            -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
            -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
            -n "$RUN_NAME" >bpv5_dio_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires BOTH:
    #  (a) the model trace shows the firmware sampled/drove our modeled pins:
    #        - bio_get(BIO5) -> 1   (externally-driven HIGH input read)
    #        - bio_put(BIO4, 1) then bio_put(BIO4, 0)  (drive HIGH then LOW)
    #        - bio_get(BIO4) -> 1 (read-back HIGH) and -> 0 (read-back LOW)
    #  (b) the firmware-rendered DIO CLI lines show those real pin values:
    #        IO5 set to INPUT: 1 / IO4 set to OUTPUT: 1 / IO4 set to INPUT: 1
    #        IO4 set to OUTPUT: 0 / IO4 set to INPUT: 0
    MODEL_IN_HIGH=$(grep -c "bio_get(BIO5) -> 1" bpv5_dio_hal.log)
    MODEL_RB_HIGH=$(grep -c "bio_get(BIO4) -> 1" bpv5_dio_hal.log)
    MODEL_RB_LOW=$(grep -c "bio_get(BIO4) -> 0" bpv5_dio_hal.log)
    MODEL_DRIVE_HI=$(grep -c "bio_put(BIO4, 1)" bpv5_dio_hal.log)
    MODEL_DRIVE_LO=$(grep -c "bio_put(BIO4, 0)" bpv5_dio_hal.log)
    # Strip ANSI from the firmware's DIO CLI lines.
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_dio_dev.log)
    FW_IN_HIGH=$(echo "$DEV_CLEAN" | grep -c "IO5 set to INPUT: 1")
    FW_OUT_HIGH=$(echo "$DEV_CLEAN" | grep -c "IO4 set to OUTPUT: 1")
    FW_RB_HIGH=$(echo "$DEV_CLEAN" | grep -c "IO4 set to INPUT: 1")
    FW_OUT_LOW=$(echo "$DEV_CLEAN" | grep -c "IO4 set to OUTPUT: 0")
    FW_RB_LOW=$(echo "$DEV_CLEAN" | grep -c "IO4 set to INPUT: 0")

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_dio_dev.log \
            && [[ "$MODEL_IN_HIGH" -ge 1 ]] \
            && [[ "$MODEL_RB_HIGH" -ge 1 && "$MODEL_RB_LOW" -ge 1 ]] \
            && [[ "$MODEL_DRIVE_HI" -ge 1 && "$MODEL_DRIVE_LO" -ge 1 ]] \
            && [[ "$FW_IN_HIGH" -ge 1 ]] \
            && [[ "$FW_OUT_HIGH" -ge 1 && "$FW_RB_HIGH" -ge 1 ]] \
            && [[ "$FW_OUT_LOW" -ge 1 && "$FW_RB_LOW" -ge 1 ]]; then
        echo "=== bpv5 DIO test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware-rendered DIO CLI pin-state lines ---"
        echo "$DEV_CLEAN" | grep -aE "IO[0-9] set to (INPUT|OUTPUT):" | head -8
        echo "--- modeled DIO pin trace (model side) ---"
        grep -aE "DioPinTarget\]" bpv5_dio_hal.log | grep -av "attached" | head -20
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no DIO transaction (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 DIO test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- firmware DIO CLI lines (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_dio_dev.log | grep -aE "IO[0-9] set to|DIO>" | tail -20 || true
echo "--- last 40 lines of bpv5_dio_dev.log (ANSI-stripped) ---"
sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g' bpv5_dio_dev.log | grep -av "Progress:" | tail -40 || true
echo "--- DioPinTarget lines in bpv5_dio_hal.log ---"
grep -aE "DioPinTarget" bpv5_dio_hal.log | grep -av Registering | tail -30 || true
echo "--- last 30 lines of bpv5_dio_hal.log ---"
grep -av "Got message" bpv5_dio_hal.log | tail -30 || true
exit 1
