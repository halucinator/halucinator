#!/usr/bin/env python3
# Copyright 2026 Christopher Wright

"""Render a captured Bus Pirate console session (raw VT100/ANSI bytes) into an
SVG "terminal screenshot" — a faithful picture of what the live interactive
terminal shows, colours and all.

The bpv5 firmware drives the console as a colour scrolling transcript: one
`ESC[2J` clear, then text in two 24-bit accent colours (gold #BFA530 prompts/
labels, green #96CB59 values) plus the default foreground. This parser handles
SGR colour (incl. `38;2;r;g;b` truecolour and the basic 30-37/90-97 set), `\r`
carriage-return overwrite, `\n` newline, and `ESC[2J` clear; it ignores the
cursor-probe sequences (`ESC[6n`, `ESC[999;999H`, `ESC[?3l`). Pure stdlib.

Usage: python3 test/firmware-rehosting/bpv5/tools/render_ansi.py session_raw.ansi -o session.svg
"""
import argparse, html, re, sys

DEFAULT_FG = "#d0d0d0"
BG = "#0c0c0c"
BASIC = {30: "#000000", 31: "#c0392b", 32: "#96cb59", 33: "#bfa530",
         34: "#3498db", 35: "#9b59b6", 36: "#1abc9c", 37: "#d0d0d0",
         90: "#7f8c8d", 91: "#e74c3c", 92: "#2ecc71", 93: "#f1c40f",
         94: "#5dade2", 95: "#bb8fce", 96: "#48c9b0", 97: "#ffffff"}


def sgr(params, cur):
    """Apply an SGR parameter list, return new fg colour."""
    nums = [int(x) if x else 0 for x in params.split(";")] if params else [0]
    i = 0
    fg = cur
    while i < len(nums):
        n = nums[i]
        if n == 0:
            fg = DEFAULT_FG
        elif n in (38, 48) and i + 1 < len(nums) and nums[i + 1] == 2:
            r, g, b = (nums[i + 2:i + 5] + [0, 0, 0])[:3]
            if n == 38:
                fg = f"#{r:02x}{g:02x}{b:02x}"
            i += 4
        elif n in BASIC:
            fg = BASIC[n]
        i += 1
    return fg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("-o", "--out", default="session.svg")
    ap.add_argument("--cols", type=int, default=80)
    a = ap.parse_args()
    data = open(a.raw, "rb").read().decode("latin1")

    lines = [[]]          # each line: list of (char, colour)
    col = 0
    fg = DEFAULT_FG
    i, n = 0, len(data)
    csi = re.compile(r"\[([0-9;?]*)([A-Za-z])")
    while i < n:
        c = data[i]
        if c == "\x1b" and i + 1 < n:
            nxt = data[i + 1]
            if nxt == "[":
                m = csi.match(data, i + 1)
                if m:
                    params, final = m.group(1), m.group(2)
                    if final == "m":
                        fg = sgr(params, fg)
                    elif final == "J" and params == "2":
                        # screen clear: keep the scrollback, mark with a divider
                        if any(ch != " " for ch, _ in lines[-1]):
                            lines.append([])
                        lines.append([(c2, "#444444") for c2 in
                                      "── screen cleared ".ljust(a.cols, "─")])
                        lines.append([])
                        col = 0
                    i = m.end()
                    continue
            elif nxt == "]":            # OSC (e.g. set-title): skip to BEL or ST
                j = i + 2
                while j < n and data[j] not in ("\x07", "\x1b"):
                    j += 1
                if j < n and data[j] == "\x1b":
                    j += 1              # consume ST's backslash too
                i = j + 1
                continue
            elif nxt in "()":           # charset designation ESC( / ESC) + 1 byte
                i += 3
                continue
            i += 1
            continue
        if c == "\n":
            lines.append([])
            col = 0
        elif c == "\r":
            col = 0
        elif c == "\t":
            col = (col // 8 + 1) * 8
        elif c >= " ":
            row = lines[-1]
            while len(row) <= col:
                row.append((" ", DEFAULT_FG))
            row[col] = (c, fg)
            col += 1
        i += 1

    # drop trailing blank lines
    while lines and not any(ch != " " for ch, _ in lines[-1]):
        lines.pop()
    if not lines:
        print("no renderable content", file=sys.stderr)
        return 1

    CW, CH, PAD = 8.4, 17, 12
    width = int(PAD * 2 + a.cols * CW)
    height = int(PAD * 2 + len(lines) * CH)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="Menlo,Consolas,monospace" font-size="13">',
           f'<rect width="{width}" height="{height}" fill="{BG}" rx="8"/>']
    for r, row in enumerate(lines):
        y = PAD + r * CH + CH - 4
        # group consecutive same-colour runs into one <text>
        x = 0
        while x < len(row):
            ch, color = row[x]
            j = x
            buf = []
            while j < len(row) and row[j][1] == color:
                buf.append(row[j][0])
                j += 1
            s = "".join(buf)
            if s.strip():
                tx = PAD + x * CW
                out.append(
                    f'<text x="{tx:.1f}" y="{y:.0f}" fill="{color}" '
                    f'xml:space="preserve">{html.escape(s)}</text>')
            x = j
    out.append("</svg>")
    open(a.out, "w").write("\n".join(out))
    print(f"wrote {a.out}: {len(lines)} lines, {width}x{height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
