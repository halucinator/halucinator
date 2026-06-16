#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# ST7789 LCD PIXEL-FRAMEBUFFER end-to-end test: boot the Bus Pirate v5 firmware
# under HALucinator, let the firmware's REAL glyph rasterizer paint the idle UI,
# capture the RGB565 pixels it streams to the ST7789 over SPI0, and dump a real
# PNG of the rendered screen (NOT a text mock).
#
# Capture approach — controller-stream (bp_handlers/bpv5/lcdfb.py / St7789Framebuffer):
#   * pixels   -> __spi_write_blocking_veneer (0x10053b58): one 16-bit RGB565
#                 pixel per call (datasize arg == 2); accumulated into a 240x320
#                 framebuffer with a CASET/RASET window cursor.
#   * window   -> lcd_set_bounding_box(x0,y0,x1,y1): programs the RAMWR window.
#   * the PNG is snapshotted every N pixels (dump_every) -> bpv5_lcd_screen.png.
#
# Full path exercised:
#   boot -> disp_default_lcd_update / ui_lcd_update / lcd_write_labels
#        -> lcd_set_bounding_box (window) + real glyph rasterizer (lcd_write_string)
#        -> per-pixel __spi_write_blocking_veneer  [intercepted -> framebuffer]
# At idle the firmware paints its left-column pin labels (Vout, IO0..IO7, GND)
# at x=15, y stepping +45, each in its table colour. PASS asserts the dumped
# framebuffer is non-trivial (>MIN_PX non-background pixels) and the PNG exists.
#
# The LCD shares NOTHING with the user SPI bus mode at this seam: the SPI flash
# keystone uses hwspi_* (SpiFlashTarget); a whole-flash xref shows every caller
# of __spi_write_blocking_veneer is LCD/display code. So run_spi_test.bash is
# unaffected.
#
# Backend: unicorn on macOS. Device launched FIRST (unicorn slow-joiner race).
# Dedicated ZMQ ports 5845/5846 + PID-scoped teardown so this never collides
# with or kills sibling agents' runs. NEVER broad-pkill.

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
TIMEOUT="${BPV5_LCDFB_TIMEOUT:-${BPV5_TIMEOUT:-90}}"

# Dedicated ZMQ ports for THIS agent (cross-wired: device tx == hal rx).
HAL_RX_PORT="${BPV5_LCDFB_HAL_RX:-5845}"
HAL_TX_PORT="${BPV5_LCDFB_HAL_TX:-5846}"

PNG_PATH="$REPO_ROOT/bpv5_lcd_screen.png"
export BPV5_LCD_PNG="$PNG_PATH"
MIN_PX="${BPV5_LCDFB_MIN_PX:-500}"

# Just boot to the shell; the LCD label row is painted at boot/idle by the
# display update path. 'h\r\n' prints the help banner whose tail is a reliable
# post-boot completion marker.
LCDFB_SCRIPT='h\r\n'
EXIT_MARKER="${BPV5_LCDFB_EXIT_MARKER:-work with pins}"
SCRIPT_DELAY="${BPV5_LCDFB_SCRIPT_DELAY:-3}"

rm -f bpv5_lcdfb_hal.log bpv5_lcdfb_dev.log "$PNG_PATH"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1

ATTEMPTS="${BPV5_LCDFB_ATTEMPTS:-3}"
attempt=0
while :; do
    attempt=$((attempt + 1))

    echo "=== [attempt $attempt/$ATTEMPTS] Launching bpv5_terminal (LCD framebuffer, ports $HAL_TX_PORT/$HAL_RX_PORT) ==="
    python3 -m halucinator.external_devices.bpv5_terminal \
            --rx-port "$HAL_TX_PORT" --tx-port "$HAL_RX_PORT" \
            --script "$LCDFB_SCRIPT" \
            --script-delay "$SCRIPT_DELAY" \
            --exit-on "$EXIT_MARKER" \
            --max-runtime "$TIMEOUT" \
            >bpv5_lcdfb_dev.log 2>&1 &
    DEV_PID=$!
    sleep 4

    echo "=== Launching halucinator (--emulator $EMULATOR) ==="
    halucinator --emulator "$EMULATOR" \
        --rx_port "$HAL_RX_PORT" --tx_port "$HAL_TX_PORT" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_config_lcdfb.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
        -n bpv5 >bpv5_lcdfb_hal.log 2>&1 &
    HAL_PID=$!

    if wait "$DEV_PID"; then DEV_RC=0; else DEV_RC=$?; fi
    { kill "$HAL_PID"; wait "$HAL_PID"; } 2>/dev/null || true

    # PASS requires: the device booted (exit marker seen) AND the framebuffer
    # model captured a non-trivial number of real pixels AND the PNG exists.
    NONBG=$(grep -aoE 'dumped .* ([0-9]+) non-bg px' bpv5_lcdfb_hal.log \
        | grep -oE '[0-9]+ non-bg' | grep -oE '[0-9]+' | sort -n | tail -1)
    NONBG="${NONBG:-0}"
    PIXELS=$(grep -aoE '([0-9]+) pixels' bpv5_lcdfb_hal.log \
        | grep -oE '[0-9]+' | sort -n | tail -1)
    PIXELS="${PIXELS:-0}"

    # Decode the PNG (pure stdlib) and assert the expected label colours are
    # present: the firmware draws the pin labels in RED (RGB565 0xf800 ->
    # 0xF8,0x00,0x00) over a GREY label panel (0x4529 -> ~0x29,0x45,0x29) and
    # WHITE (0xffff). A real rendered screen has thousands of red + grey px;
    # a blank/garbage capture would not.
    COLOR_OK=0
    if [[ -f "$PNG_PATH" ]]; then
        COLOR_OK=$(python3 - "$PNG_PATH" <<'PY'
import sys, struct, zlib
d = open(sys.argv[1], "rb").read()
# Parse IHDR + concatenated IDAT, inflate, un-filter (filter 0 rows only).
w, h = struct.unpack(">II", d[16:24])
i = 8; idat = b""
while i < len(d):
    ln = struct.unpack(">I", d[i:i+4])[0]; tag = d[i+4:i+8]
    if tag == b"IDAT": idat += d[i+8:i+8+ln]
    i += 12 + ln
raw = zlib.decompress(idat)
stride = w*3
red = grey = 0
for y in range(h):
    row = raw[y*(stride+1)+1 : y*(stride+1)+1+stride]
    for x in range(0, stride, 3):
        r, g, b = row[x], row[x+1], row[x+2]
        if r > 180 and g < 80 and b < 80: red += 1
        # Label-panel grey is ~ (41,40,41): near-neutral, mid-low luminance.
        elif 25 <= r <= 70 and 25 <= g <= 70 and 25 <= b <= 70 \
                and abs(r-g) < 20 and abs(g-b) < 20: grey += 1
# Expect a substantial label panel: thousands of red glyph px + grey panel px.
print(1 if (red >= 1000 and grey >= 1000) else 0)
PY
)
    fi
    COLOR_OK="${COLOR_OK:-0}"

    if [[ "$DEV_RC" -eq 0 ]] \
            && grep -q "exit marker .* seen" bpv5_lcdfb_dev.log \
            && [[ -f "$PNG_PATH" ]] \
            && [[ "$NONBG" -ge "$MIN_PX" ]] \
            && [[ "$COLOR_OK" -eq 1 ]]; then
        echo "=== bpv5 LCD framebuffer test PASSED (--emulator $EMULATOR, attempt $attempt) ==="
        echo "--- captured framebuffer ---"
        echo "PNG:        $PNG_PATH ($(wc -c < "$PNG_PATH" | tr -d ' ') bytes)"
        echo "non-bg px:  $NONBG  (>= $MIN_PX required)"
        echo "pixels fed: $PIXELS"
        echo "colours:    red+grey label panel present (COLOR_OK=$COLOR_OK)"
        grep -a '\[LcdFb\] dumped' bpv5_lcdfb_hal.log | tail -3
        exit 0
    fi

    if [[ "$attempt" -ge "$ATTEMPTS" ]]; then
        break
    fi
    echo "=== [attempt $attempt/$ATTEMPTS] no framebuffer capture (slow-joiner flake?) — retrying ==="
    sleep 2
done

echo "=== bpv5 LCD framebuffer test FAILED (--emulator $EMULATOR, device exit=$DEV_RC) ==="
echo "non-bg px=$NONBG pixels=$PIXELS color_ok=${COLOR_OK:-?} png=$( [[ -f "$PNG_PATH" ]] && echo yes || echo no )"
echo "--- last 30 lines of bpv5_lcdfb_dev.log ---"
tail -30 bpv5_lcdfb_dev.log || true
echo "--- modeled LCD framebuffer capture (if any) ---"
grep -aE '\[LcdFb\]' bpv5_lcdfb_hal.log | head -40 || true
echo "--- last 40 lines of bpv5_lcdfb_hal.log ---"
grep -av "Got message" bpv5_lcdfb_hal.log | tail -40 || true
exit 1
