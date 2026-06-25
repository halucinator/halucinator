#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Capture a Bus Pirate console session as raw VT100/ANSI bytes (colour
# preserved) and render it to an SVG "terminal screenshot" — the same output a
# live interactive session shows. Drives a scripted session (default: open the
# mode menu, enter SPI, run the JEDEC-ID read) so the capture is reproducible.
#
# Usage:  bash test/firmware-rehosting/bpv5/tools/capture_session.bash [out.svg] ['<keystroke script>']
#   e.g.  bash test/firmware-rehosting/bpv5/tools/capture_session.bash i2c.svg 'm\r5\r\r\r[0xA0 0x00 [0xA1 r:2]\r'
set +e; set +m
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"; cd "$ROOT"
VENV="$ROOT/virtualenvs/halucinator/bin"
[[ -x "$VENV/halucinator" ]] && export PATH="$VENV:$PATH"
export PYTHONPATH=".:src"; export PYTHONUNBUFFERED=1

OUT="${1:-bpv5_session.svg}"
SCRIPT="${2:-m\r6\r\r\r\r\r\r[0x9f r:3]\r}"
RAW="$(mktemp -t bpv5ansi.XXXXXX)"
RX=5855; TX=5856   # dedicated ports; scoped teardown below

python3 -m halucinator.external_devices.bpv5_terminal --rx-port "$TX" --tx-port "$RX" \
        --script "$SCRIPT" --script-delay 5 --exit-on 'CS Disabled' \
        --max-runtime 70 >"$RAW" 2>/dev/null &
DEV=$!
sleep 3
halucinator --emulator unicorn --rx_port "$RX" --tx_port "$TX" \
        -c test/firmware-rehosting/bpv5/bpv5_memory.yaml -c test/firmware-rehosting/bpv5/bpv5_config.yaml \
        -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml -n bpv5 >/dev/null 2>&1 &
H=$!
wait "$DEV" 2>/dev/null
{ kill "$H"; wait "$H"; } 2>/dev/null

python3 test/firmware-rehosting/bpv5/tools/render_ansi.py "$RAW" -o "$OUT"
rm -f "$RAW"
echo "screenshot: $OUT"
