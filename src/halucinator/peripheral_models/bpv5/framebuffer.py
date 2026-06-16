#!/usr/bin/env python3
# Copyright 2026 Christopher Wright

"""Pure-stdlib RGB565 framebuffer -> PNG encoder (no Pillow / numpy).

The HALucinator venv has NO third-party imaging libraries, so this module
encodes a PNG using only ``zlib`` + ``struct``: an 8-bit truecolour (RGB)
image is written as IHDR + a single zlib-compressed IDAT of filter-0
scanlines + IEND. ~40 lines of real work.

Two layers:

* :class:`Framebuffer` — a width*height grid of RGB565 (u16) pixels with a
  ST7789-style windowed RAM-write cursor (CASET/RASET window + RAMWR auto
  increment), plus RGB565->RGB888 expansion and PNG emit. This is what the
  LCD handler accumulates into.
* :func:`write_png` / :func:`rgb_rows_to_png` — the bare encoder, importable
  and usable on any list of (r,g,b) rows.

CLI: ``python3 fb_to_png.py in.raw out.png W H [--byteswap]`` converts a raw
RGB565 little-endian dump to a PNG (handy for ad-hoc framebuffer dumps).
"""
from __future__ import annotations

import struct
import zlib
from typing import List, Sequence, Tuple


# --------------------------------------------------------------------------
# Bare PNG encoder (filter-0 truecolour, 8-bit).
# --------------------------------------------------------------------------
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path: str, width: int, height: int, rgb_bytes: bytes) -> None:
    """Write an 8-bit RGB PNG. ``rgb_bytes`` is width*height*3 bytes, row-major
    (R,G,B per pixel), top row first."""
    if len(rgb_bytes) != width * height * 3:
        raise ValueError(
            f"rgb_bytes len {len(rgb_bytes)} != {width*height*3}")
    # Prepend a filter-type byte (0 = None) to each scanline, then deflate.
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgb_bytes[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, RGB
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


def rgb_rows_to_png(path: str, rows: Sequence[Sequence[Tuple[int, int, int]]]
                    ) -> None:
    """Write a PNG from a list of rows, each a list of (r,g,b) tuples."""
    height = len(rows)
    width = len(rows[0]) if height else 0
    buf = bytearray()
    for row in rows:
        for (r, g, b) in row:
            buf.append(r & 0xFF)
            buf.append(g & 0xFF)
            buf.append(b & 0xFF)
    write_png(path, width, height, bytes(buf))


def rgb565_to_rgb888(c: int) -> Tuple[int, int, int]:
    """Expand a 16-bit RGB565 colour to 8-bit per channel.

    The ST7789 wire/byte order for these pixels is little-endian RGB565
    (low byte first). ``c`` here is already the host u16 value (R in the high
    5 bits): bits [15:11]=R, [10:5]=G, [4:0]=B.
    """
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    # Replicate high bits into low bits for a full 0..255 range.
    r = (r5 << 3) | (r5 >> 2)
    g = (g6 << 2) | (g6 >> 4)
    b = (b5 << 3) | (b5 >> 2)
    return r, g, b


# --------------------------------------------------------------------------
# ST7789-style framebuffer with a windowed RAM-write cursor.
# --------------------------------------------------------------------------
class Framebuffer:
    """A width*height RGB565 framebuffer driven exactly like the ST7789 GRAM.

    The firmware programs a column/row window (CASET 0x2A / RASET 0x2B) then
    streams pixels (RAMWR 0x2C) that auto-increment column-fastest, wrapping
    to the next row at the right edge of the window, until the window is
    filled. :meth:`set_window` resets the cursor; :meth:`push_pixel` paints
    one RGB565 pixel at the cursor and advances it.
    """

    def __init__(self, width: int = 240, height: int = 320,
                 bg: int = 0x0000) -> None:
        self.width = width
        self.height = height
        self.bg = bg & 0xFFFF
        # Row-major store of u16 RGB565, initialised to the background.
        self.buf: List[int] = [self.bg] * (width * height)
        # Active window (x0,y0,x1,y1) inclusive + RAMWR cursor.
        self._x0 = 0
        self._y0 = 0
        self._x1 = width - 1
        self._y1 = height - 1
        self._cx = 0
        self._cy = 0
        self.pixels_written = 0

    def set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Program the CASET/RASET window and reset the RAMWR cursor."""
        # Clamp + normalise (some callers pass start/end, tolerate swap).
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        self._x0 = max(0, min(self.width - 1, x0))
        self._y0 = max(0, min(self.height - 1, y0))
        self._x1 = max(0, min(self.width - 1, x1))
        self._y1 = max(0, min(self.height - 1, y1))
        self._cx = self._x0
        self._cy = self._y0

    def push_pixel(self, color565: int) -> None:
        """Paint one RGB565 pixel at the RAMWR cursor; advance (col-fastest)."""
        x, y = self._cx, self._cy
        if 0 <= x < self.width and 0 <= y < self.height:
            self.buf[y * self.width + x] = color565 & 0xFFFF
            self.pixels_written += 1
        # Advance within the window, wrapping column then row.
        self._cx += 1
        if self._cx > self._x1:
            self._cx = self._x0
            self._cy += 1
            if self._cy > self._y1:
                self._cy = self._y0  # wrap (matches GRAM auto-rewind)

    def count_non_bg(self) -> int:
        bg = self.bg
        return sum(1 for c in self.buf if c != bg)

    def to_rgb888_bytes(self) -> bytes:
        out = bytearray(self.width * self.height * 3)
        i = 0
        for c in self.buf:
            r, g, b = rgb565_to_rgb888(c)
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            i += 3
        return bytes(out)

    def rotated_rgb888(self, rotate: int) -> Tuple[int, int, bytes]:
        """Return (W, H, rgb888 bytes) rotated by ``rotate`` degrees (0/90/180/
        270, clockwise). The BPv5 GRAM is landscape but the panel is mounted so
        the UI reads with a 90deg CW rotation; this lets the saved PNG be shown
        upright while the framebuffer stays pixel-faithful to the GRAM."""
        rotate %= 360
        w, h = self.width, self.height
        src = self.buf
        if rotate == 0:
            return w, h, self.to_rgb888_bytes()
        if rotate in (90, 270):
            nw, nh = h, w
        else:
            nw, nh = w, h
        dst = [0] * (nw * nh)
        for y in range(h):
            for x in range(w):
                c = src[y * w + x]
                if rotate == 90:        # CW
                    nx, ny = (h - 1 - y), x
                elif rotate == 180:
                    nx, ny = (w - 1 - x), (h - 1 - y)
                else:                   # 270 CW
                    nx, ny = y, (w - 1 - x)
                dst[ny * nw + nx] = c
        out = bytearray(nw * nh * 3)
        i = 0
        for c in dst:
            r, g, b = rgb565_to_rgb888(c)
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            i += 3
        return nw, nh, bytes(out)

    def save_png(self, path: str, rotate: int = 0) -> None:
        w, h, data = self.rotated_rgb888(rotate)
        write_png(path, w, h, data)


# --------------------------------------------------------------------------
# CLI: raw RGB565 dump -> PNG.
# --------------------------------------------------------------------------
def _main(argv: List[str]) -> int:
    import sys
    if len(argv) < 4:
        print("usage: fb_to_png.py in.raw out.png W H [--byteswap]",
              file=sys.stderr)
        return 2
    in_path, out_path = argv[0], argv[1]
    w, h = int(argv[2]), int(argv[3])
    byteswap = "--byteswap" in argv[4:]
    with open(in_path, "rb") as f:
        raw = f.read()
    fb = Framebuffer(w, h)
    for i in range(min(w * h, len(raw) // 2)):
        lo = raw[2 * i]
        hi = raw[2 * i + 1]
        c = (lo | (hi << 8)) if not byteswap else (hi | (lo << 8))
        fb.buf[i] = c
    fb.save_png(out_path)
    print(f"wrote {out_path} ({w}x{h}, {fb.count_non_bg()} non-bg px)")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
