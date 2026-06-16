#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Launch halucinator with the bpv5 demo configs PLUS the live LCD framebuffer
# overlay, so the St7789Framebuffer model captures pixels for the live LCD
# panel:  python3 -m halucinator.external_devices.bpv5_lcd_panel
#
# Same as run.sh but with bpv5_config_lcdfb.yaml added and BPV5_LCD_PNG set to
# a shared absolute path the panel defaults to. Honours HAL_EMULATOR (default
# unicorn on macOS) — the per-pixel framebuffer capture is fast on unicorn and
# slow on the round-trip backends, so unicorn is recommended for live use.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src:."
export PYTHONUNBUFFERED=1
export BPV5_LCD_PNG="${BPV5_LCD_PNG:-/tmp/bpv5_lcd.png}"
export BPV5_ADC_FILE="${BPV5_ADC_FILE:-/tmp/bpv5_adc.json}"
rm -f "$BPV5_ADC_FILE"   # clean slate: no stale live IO overrides from a prior run

if [[ -n "$HAL_EMULATOR" ]]; then
    EMULATOR="$HAL_EMULATOR"
elif [[ "$(uname -s)" == "Darwin" ]]; then
    EMULATOR="unicorn"
else
    EMULATOR="unicorn"
fi

export BPV5_HAL_LOG="${BPV5_HAL_LOG:-/tmp/bpv5_hal.log}"
echo "[run_lcd] backend=$EMULATOR  LCD=$BPV5_LCD_PNG  log=$BPV5_HAL_LOG"
# tee to the log so the web panel (bpv5_panel) can show modeled-device activity.
halucinator --emulator "$EMULATOR" \
    -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_config_lcdfb.yaml \
    -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \
    -n bpv5 2>&1 | tee "$BPV5_HAL_LOG"
