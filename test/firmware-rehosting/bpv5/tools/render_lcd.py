#!/usr/bin/env python3
# Copyright 2026 Christopher Wright

"""Render the modeled ST7789 LCD into an SVG mock of the physical screen.

The LCD model (`bp_handlers/bpv5/lcd.py`) captures every `lcd_write_string` the
firmware draws, with the string, its (x, y) window origin, and fg/bg colours
(RGB565). This tool parses those `[Lcd] write_string(...)` lines from a
halucinator run log and lays the text out on a screen-shaped SVG — a literal
"this is what would be on the 320x240 TFT" picture. Pure stdlib (no Pillow).

Usage:
    python3 test/firmware-rehosting/bpv5/tools/render_lcd.py <hal.log> [-o lcd.svg]
    # produce a log first, e.g.:
    #   bash test/firmware-rehosting/bpv5/run_lcd_test.bash      (writes bpv5_lcd_hal.log)
"""
import argparse, html, re, sys

LINE = re.compile(
    r'\[Lcd\]\s+write_string\("(?P<t>.*?)",\s*x=(?P<x>-?\d+),\s*y=(?P<y>-?\d+),'
    r'\s*fg=0x(?P<fg>[0-9a-fA-F]+),\s*bg=0x(?P<bg>[0-9a-fA-F]+)\)'
)


def rgb565(c):
    r = ((c >> 11) & 0x1F) << 3
    g = ((c >> 5) & 0x3F) << 2
    b = (c & 0x1F) << 3
    return f"#{r:02x}{g:02x}{b:02x}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("-o", "--out", default="lcd.svg")
    a = ap.parse_args()

    draws, seen = [], set()
    for ln in open(a.log, encoding="utf-8", errors="replace"):
        m = LINE.search(ln)
        if m:
            rec = (m["t"], int(m["x"]), int(m["y"]),
                   int(m["fg"], 16), int(m["bg"], 16))
            if rec in seen:          # collapse the firmware's repeated redraws
                continue
            seen.add(rec)
            draws.append(rec)
    if not draws:
        print("no [Lcd] write_string lines found in", a.log, file=sys.stderr)
        return 1

    # Fit the observed coordinate space (the firmware's UI space can exceed the
    # nominal 320x240; scale to fit while keeping the screen aspect).
    maxx = max(x for _, x, _, _, _ in draws) + 60
    maxy = max(y for _, _, y, _, _ in draws) + 24
    W, H = max(320, maxx), max(240, maxy)
    # Screen background = the most common bg colour seen.
    from collections import Counter
    bg0 = Counter(bg for *_, bg in draws).most_common(1)[0][0]

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="monospace">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="{rgb565(bg0)}" '
           f'rx="10"/>',
           f'<rect x="2" y="2" width="{W-4}" height="{H-4}" fill="none" '
           f'stroke="#444" rx="8"/>']
    for t, x, y, fg, bg in draws:
        if not t.strip():
            continue
        out.append(f'<text x="{x}" y="{y+14}" font-size="15" '
                   f'fill="{rgb565(fg)}">{html.escape(t)}</text>')
    out.append("</svg>")
    open(a.out, "w").write("\n".join(out))
    print(f"wrote {a.out}: {len(draws)} draws, {W}x{H}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
