#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Onboard SPI-NAND storage end-to-end test: boot the Bus Pirate v5 firmware
# under HALucinator, run the CLI `ls` command at the HiZ> prompt, and assert the
# firmware renders the *modeled* FatFs directory listing that the NandStorage
# handler serves — then optionally `cat` a modeled file to prove file content
# flows through too.
#
# Full path exercised:
#   CLI 'ls'
#     -> disk_ls_handler -> storage_ls
#     -> f_opendir / f_readdir (xN) / f_closedir   (FatFs ABI)
#     -> NandStorage model (modeled SPI-NAND FatFs volume)
#     -> firmware's storage_ls print loop renders the entries on the terminal.
#   CLI 'cat bpconfig.bp'
#     -> disk_cat_handler -> f_open / f_gets (xN) / f_close
#     -> NandStorage serves the modeled bpconfig.bp lines
#     -> firmware prints the file contents.
#
# Storage mounts during boot (storage_mount -> f_mount -> FR_OK), so this also
# confirms the model doesn't break the boot path.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). The device is
# launched FIRST (the unicorn slow-joiner race — see run_tests.bash). DEDICATED
# ZMQ ports 5825/5826 + PID-scoped teardown so this never collides with or kills
# sibling agents' runs. NO broad pkill.

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
TIMEOUT="${BPV5_NAND_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# DEDICATED ZMQ ports for this agent (cross-wired: device tx == hal rx).
HAL_RX_PORT="${BPV5_NAND_HAL_RX:-5825}"
HAL_TX_PORT="${BPV5_NAND_HAL_TX:-5826}"

# At HiZ> run `ls`, then `cat` the modeled config file. No mode entry needed —
# these are top-level shell commands.
NAND_SCRIPT='ls\r cat bpconfig.bp\r'
# storage_ls prints "<n> dirs, <n> files" as its footer; disk_cat finishes after
# the config lines. Exit on the cat'd config's closing brace marker.
EXIT_MARKER="${BPV5_NAND_EXIT_MARKER:-led_brightness}"
SCRIPT_DELAY="${BPV5_NAND_SCRIPT_DELAY:-9}"

rm -f bpv5_nand_hal.log bpv5_nand_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_NAND_ATTEMPTS:-3}"
attempt=0
while :; do
    attempt=$((attempt + 1))

    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (storage ls/cat, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$NAND_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_nand_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >bpv5_nand_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires:
    #  (a) the model log shows f_mount FR_OK + the directory was served;
    #  (b) the firmware-rendered terminal shows our modeled filenames in the ls
    #      listing AND the modeled config content from cat.
    MODEL_MOUNT=$(grep -c "f_mount() -> FR_OK" bpv5_nand_hal.log)
    MODEL_READDIR=$(grep -c "f_readdir() ->" bpv5_nand_hal.log)
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g' bpv5_nand_dev.log)
    HAS_LS_CONFIG=$(printf '%s' "$DEV_CLEAN" | grep -c "bpconfig.bp")
    HAS_LS_README=$(printf '%s' "$DEV_CLEAN" | grep -c "readme.txt")
    HAS_DIR=$(printf '%s' "$DEV_CLEAN" | grep -c "<DIR>")
    HAS_CAT=$(printf '%s' "$DEV_CLEAN" | grep -c "led_brightness")

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_nand_dev.log \
            && [[ "$MODEL_MOUNT" -ge 1 ]] \
            && [[ "$MODEL_READDIR" -ge 1 ]] \
            && [[ "$HAS_LS_CONFIG" -ge 1 && "$HAS_LS_README" -ge 1 ]] \
            && [[ "$HAS_CAT" -ge 1 ]]; then
        echo "=== bpv5 NAND storage test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- firmware-rendered ls listing + cat content (ANSI-stripped) ---"
        printf '%s\n' "$DEV_CLEAN" | grep -aE "<DIR>|\.bp|\.scr|\.txt|led_brightness|terminal_|dirs, .* files" | head -30
        echo "--- modeled-storage served ops ---"
        grep -aE "\[Storage\]" bpv5_nand_hal.log
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no listing (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 NAND storage test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 40 lines of bpv5_nand_dev.log ---"
tail -40 bpv5_nand_dev.log || true
echo "--- modeled-storage served ops (if any) ---"
grep -aE "\[Storage\]" bpv5_nand_hal.log || true
echo "--- last 40 lines of bpv5_nand_hal.log ---"
grep -av "Got message" bpv5_nand_hal.log | tail -40 || true
exit 1
