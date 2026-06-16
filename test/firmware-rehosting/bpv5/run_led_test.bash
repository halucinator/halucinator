#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# LED end-to-end test: boot the Bus Pirate v5 firmware under HALucinator,
# enter LED mode (menu #10) through the CLI, select the WS2812 strip type, and
# drive a distinctive colour. LED mode is OUTPUT-ONLY (a PIO-bit-banged
# WS2812/APA102 strip), so "working" = the model CAPTURES the exact pixel word
# the firmware emits AND the firmware's own CLI echoes the write.
#
# Full path exercised:
#   CLI '[0x80FF00]'
#     -> hwled_start / hwled_write / hwled_stop  (mode vtable)
#     -> ws2812_write(r0 = 0x80FF00)             (PIO leaf write helper, HLE'd)
#     -> LedStripSink model captures the emitted pixel word
#     -> firmware renders its own write echo on the terminal.
#
# Colour sent: 0x80FF00  (WS2812 wire order is G,R,B; the firmware does NO
# RGB->GRB reorder, so 0x80FF00 = G=0x80 R=0xFF B=0x00). On the wire this is
# the bytes 80 FF 00, i.e. a pixel with RED=0xFF GREEN=0x80 BLUE=0x00 — a
# distinctive orange the captured bytes make unmistakable.
#
# Backend: unicorn on macOS (avatar2/qemu are Linux-only). The device is
# launched FIRST (the unicorn slow-joiner race — see run_tests.bash).
# DEDICATED ZMQ ports 5765/5766 + PID-scoped teardown so this never collides
# with or kills sibling agents' runs (NEVER broad-pkill).

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
TIMEOUT="${BPV5_LED_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# DEDICATED ports for the LED agent (sibling agents use other ports).
HAL_RX_PORT="${BPV5_LED_HAL_RX:-5765}"
HAL_TX_PORT="${BPV5_LED_HAL_TX:-5766}"

# Mode-entry + transaction. After menu #10 (LED), the only setup prompt is
# "LED type" — send '1' for WS2812/NeoPixel (single-wire). Then frame a write:
#   [0x80FF00]  -> hwled_start, write the GRB word 0x80FF00, hwled_stop
# (no pixel-count prompt; one value written == one pixel emitted).
LED_SCRIPT='m\r10\r1\r[0x80FF00]\r'
# After the write executes, the firmware renders the transaction as
# (ANSI-stripped):  RESET  /  TX: 0x..  /  RESET  /  LED>  — the "RESET" labels
# are the WS2812 start/stop frame markers and "TX:" is the value echo (the
# LED-mode analogue of SPI's "CS Enabled"/"TX:"). "TX:" only appears AFTER the
# write, so it is the reliable post-transaction exit marker (the bare "LED>"
# also matches the PRE-transaction prompt and would exit too early).
EXIT_MARKER="${BPV5_LED_EXIT_MARKER:-TX:}"
SCRIPT_DELAY="${BPV5_LED_SCRIPT_DELAY:-9}"

# Expected captured WS2812 pixel word (model log) and wire bytes.
EXPECT_WORD="0x80FF00"
EXPECT_WIRE="G=0x80 R=0xFF B=0x00"

# --- cleanup -------------------------------------------------------------
# Do NOT broad-pkill — sibling agents share this host. We only kill the PIDs
# we spawn (DEV_PID/HAL_PID).
rm -f bpv5_led_hal.log bpv5_led_dev.log

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_LED_ATTEMPTS:-3}"
attempt=0
while :; do
    attempt=$((attempt + 1))

    # --- device first (slow-joiner) --------------------------------------
    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (LED WS2812 write, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$LED_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_led_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    # --- halucinator -----------------------------------------------------
    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >bpv5_led_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # --- evaluate --------------------------------------------------------
    # PASS requires BOTH:
    #  (a) the model CAPTURED the exact WS2812 pixel word the firmware emitted
    #      (proving the pixel data really flowed through the PIO leaf helper) —
    #      this is the authoritative colour proof, since LED mode's 24-bit
    #      value is shown only 1 byte wide on the CLI ("TX: 0x00", a firmware
    #      display-width quirk);
    #  (b) the firmware itself ACCEPTED + RENDERED the write through its own CLI
    #      (the RESET start/stop frame labels + the TX: value echo).
    MODEL_PIXEL=$(grep -c "WS2812 PIXEL word=0x80FF00" bpv5_led_hal.log)
    MODEL_WIRE=$(grep -c "wire bytes G=0x80 R=0xFF B=0x00" bpv5_led_hal.log)
    # Firmware CLI confirmation (ANSI-stripped): the LED write transaction
    # renders "RESET" (frame start/stop) and a "TX:" value echo.
    DEV_CLEAN=$(sed $'s/\x1b\\[[0-9;?]*[A-Za-z]//g; s/\x1b\\][0-9];//g; s/\x07//g; s/\x1b//g' bpv5_led_dev.log)
    DEV_TX=$(printf '%s\n' "$DEV_CLEAN" | grep -a "TX:" | head -1)
    DEV_RESET=$(printf '%s\n' "$DEV_CLEAN" | grep -ac "RESET")

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_led_dev.log \
            && [[ "$MODEL_PIXEL" -ge 1 ]] \
            && [[ "$MODEL_WIRE" -ge 1 ]] \
            && [[ -n "$DEV_TX" ]] \
            && [[ "$DEV_RESET" -ge 1 ]]; then
        echo "=== bpv5 LED test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- colour sent: $EXPECT_WORD (WS2812 wire order $EXPECT_WIRE = R=0xFF G=0x80 B=0x00) ---"
        echo "--- firmware CLI transaction render (RESET frame + TX echo) ---"
        printf '%s\n' "$DEV_CLEAN" | grep -aE "RESET|TX:" | head -4
        echo "--- model captured pixel frame ---"
        grep -aE "LedStripSink\]" bpv5_led_hal.log
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no capture (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 LED test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "--- last 40 lines of bpv5_led_dev.log ---"
tail -40 bpv5_led_dev.log || true
echo "--- model capture (if any) ---"
grep -aE "LedStripSink\]" bpv5_led_hal.log || true
echo "--- last 40 lines of bpv5_led_hal.log ---"
grep -av "Got message" bpv5_led_hal.log | tail -40 || true
exit 1
