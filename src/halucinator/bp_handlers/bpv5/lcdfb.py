# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — ST7789 LCD **pixel framebuffer** model.

Where ``bpv5_handlers_lcd.py`` (class ``St7789Lcd``) only captured the drawn
text *strings* (HLE at ``lcd_write_string``, short-circuiting the rasterizer),
this module captures the **actual pixels** the firmware's real glyph
rasterizer emits, and accumulates them into a 240x320 RGB565 framebuffer that
is dumped to a real PNG.

CAPTURE APPROACH — controller-stream (most faithful)
====================================================
RE (capstone Thumb, flash base 0x10000000) found the ST7789 is driven over
**SPI0 (0x4003c000)** with the D/C and CS lines on RP2040 SIO GPIO. Two leaf
primitives carry every LCD byte:

* ``spi_byte`` veneer @ ``0x10001334`` — ``(r0=rw, r1=ctx, r2=byte)`` sends one
  COMMAND/DATA *byte*. Used for the ST7789 command opcodes (CASET 0x2A,
  RASET 0x2B, RAMWR 0x2C) and the window-coordinate bytes, with the D/C SIO
  GPIO line toggled around them.
* ``spi_push`` veneer @ ``0x10053b58`` — ``(r0=0x4003c000 SPI0, r1=value,
  r2=datasize)`` pushes ONE element to the SPI FIFO: ``r2==2`` => one 16-bit
  RGB565 **pixel** (``r1`` = the colour), ``r2==1`` => an 8-bit byte. It is
  NOT a run/DMA op — a pixel run is an explicit ``for`` loop in
  ``lcd_write_string`` calling it once per pixel.

The high-level window seam is ``lcd_set_bounding_box(x0, y0, x1, y1)`` (it
programs CASET/RASET); we hook it to set the framebuffer's RAM-write window.
Each ``spi_push`` 16-bit pixel then lands at the next position scanning that
window column-fastest (ST7789 RAMWR auto-increment) — exactly the order the
real panel would latch it. ``lcd_write_background`` / ``lcd_write_string`` /
``lcd_write_labels`` / ``ui_lcd_update`` all flow through this same seam, so
the captured framebuffer is pixel-for-pixel what the firmware drew.

SHARED-SSP SAFETY
=================
The LCD is on **SPI0 (0x4003c000)** via the ``lcd_*`` leaves; the user-facing
SPI bus mode is the modeled SPI NOR flash on the ``hwspi_*`` leaves
(``SpiFlashTarget``). These are DISTINCT symbols — we hook only the LCD path
(``lcd_set_bounding_box`` + the two ``spi_*`` veneers via dedicated symbol
names ``lcd_spi_push`` / ``lcd_spi_byte`` mapped to 0x10053b58 / 0x10001334),
so the SPI flash keystone (``run_spi_test.bash``) is untouched. Our pixel
hook returns ``(True, 0)`` for the push veneer to skip the real SPI-FIFO MMIO
write (which would spin on an unmodeled PL022/SPI0), and lets the byte helper
run as a no-op too.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.bpv5.framebuffer import Framebuffer

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# Default panel geometry: the BPv5 ST7789 runs LANDSCAPE 320x240 (MADCTL=0x20,
# COLMOD=0x55 RGB565 — confirmed in lcd_configure and the lcd_write_background
# full-screen fill CASET x_end=0x140=320 / RASET y_end=0xF0=240).
_DEF_W = 320
_DEF_H = 240


class St7789Framebuffer(BPHandler):
    """Modeled ST7789 that paints the firmware's real pixels into a PNG.

    Hooks (wired in ``bpv5_config.yaml``):

    * ``set_window`` -> ``lcd_set_bounding_box(x0,y0,x1,y1)`` — program the
      CASET/RASET window + reset the RAMWR cursor.
    * ``pixel`` -> ``lcd_spi_push`` (the 0x10053b58 veneer) — on a 16-bit
      transfer (``r2==2``) paint one RGB565 pixel (colour in ``r1``) at the
      cursor; skip the real SPI-FIFO write.
    * ``byte`` -> ``lcd_spi_byte`` (the 0x10001334 veneer) — command/data
      bytes; we let them no-op (the window comes from set_bounding_box).
    * ``init`` / ``configure`` / ``backlight`` — modeled no-ops (replace the
      old ``skip_lcd_*``; keep the real SPI-driving bring-up off the bus).
    * ``dump`` -> ``ui_lcd_update`` (optional) or called at teardown — write
      the accumulated framebuffer to ``png_path``.
    """

    def __init__(self, width: int = _DEF_W, height: int = _DEF_H,
                 png_path: Optional[str] = None,
                 byteswap_color: bool = True,
                 dump_every: int = 0) -> None:
        super().__init__()
        self.fb = Framebuffer(width, height, bg=0x0000)
        self.png_path = png_path or os.environ.get(
            "BPV5_LCD_PNG", "bpv5_lcd_screen.png")
        # Firmware stores RGB565 colours byte-swapped relative to the host
        # u16 (low byte first on the wire); swap before painting so the PNG
        # shows true colours. Tunable via class_args for empirical checks.
        self.byteswap = byteswap_color
        self.dump_every = dump_every
        self._push_calls = 0
        self._windows = 0
        # ST7789 command FSM state.
        self._cmd = 0              # last command opcode
        self._expect = 0           # coordinate data bytes still pending
        self._coords: list = []    # accumulated coordinate bytes
        self._win_x = (0, width - 1)
        self._win_y = (0, height - 1)
        # Optional raw stream trace (BPV5_LCD_TRACE=path) for offline render
        # iteration: 'B xx' per command/data byte, 'P xxxx' per RGB565 pixel.
        self._trace = None
        _tp = os.environ.get("BPV5_LCD_TRACE")
        if _tp:
            try:
                self._trace = open(_tp, "w")  # noqa: SIM115
            except Exception:  # noqa: BLE001
                self._trace = None
        print(f"[LcdFb] modeled ST7789 {width}x{height} framebuffer attached "
              f"(controller-stream capture -> {self.png_path})", flush=True)

    # --- ST7789 controller stream decode (CASET/RASET/RAMWR) -------------
    @bp_handler(["pixel"])
    def pixel(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``spi_write_blocking(spi0, src, datasize)`` — one element to the FIFO.

        This single veneer carries the WHOLE ST7789 byte stream:

        * ``datasize == 1`` => an 8-bit transfer where ``src`` (arg1) is a
          POINTER to a 1-byte buffer. It is either a COMMAND opcode (CASET
          0x2A / RASET 0x2B / RAMWR 0x2C / ...) or a window-coordinate DATA
          byte. We tell them apart with a self-describing protocol FSM: when no
          data bytes are pending the byte is a command (which then sets how
          many coordinate bytes follow); otherwise it's coordinate data. The
          D/C GPIO line is RAM-backed (SET/CLR don't update the read register
          under emulation), so this opcode-driven FSM is the reliable
          discriminator — CASET/RASET each carry a fixed 4-byte payload
          (start_hi, start_lo, end_hi, end_lo), big-endian, and the stream is
          perfectly self-framing (verified: ``2A 00 00 01 40  2B 00 00 00 F0
          2C ...`` = full-screen window 0..0x140 x 0..0xF0 then RAMWR).
        * ``datasize == 2`` => one 16-bit RGB565 PIXEL. ``src`` (arg1) is a
          POINTER to the 2-byte colour in the firmware's colour table (e.g.
          0x100b11c4=0x00f8 red, 0x100b11dc=0x4529 grey bg) — NOT the value —
          so we DEREFERENCE it. Painted at the RAMWR cursor inside the current
          CASET/RASET window (the rasterizer streams exactly window-area
          pixels in GRAM auto-increment order, so a sequential cursor is exact).

        We return ``(True, 0)`` to skip the real SPI0 FIFO write (unmodeled).
        """
        size = qemu.get_arg(2) & 0xFF
        if size == 2:
            # arg1 is a POINTER to the 2-byte RGB565 colour (the rasterizer
            # passes &fg / &bg from the colour table), NOT the value itself.
            ptr = qemu.get_arg(1)
            try:
                color = qemu.read_memory(ptr, 2, 1) & 0xFFFF
            except Exception:  # noqa: BLE001
                color = 0
            if self._trace is not None:
                self._trace.write(f"P {color:04x}\n")
            if self.byteswap:
                color = ((color & 0xFF) << 8) | (color >> 8)
            self.fb.push_pixel(color)
            self._push_calls += 1
            if self.dump_every and (self._push_calls % self.dump_every) == 0:
                self._dump()
            return True, 0

        if size == 1:
            ptr = qemu.get_arg(1)
            try:
                b = qemu.read_memory(ptr, 1, 1) & 0xFF
            except Exception:  # noqa: BLE001
                return True, 0
            if self._trace is not None:
                self._trace.write(f"B {b:02x}\n")
            self._feed_byte(b)
        return True, 0

    def _feed_byte(self, b: int) -> None:
        """ST7789 command/data byte FSM: track CASET/RASET window + RAMWR."""
        if self._expect > 0:
            # Coordinate data byte for the current command.
            self._coords.append(b)
            self._expect -= 1
            if self._expect == 0:
                self._apply_coords()
            return
        # A command opcode.
        self._cmd = b
        self._coords = []
        if b in (0x2A, 0x2B):      # CASET / RASET — 4 data bytes (start,end)
            self._expect = 4
        else:
            self._expect = 0       # RAMWR (0x2C) and others: no tracked args

    def _apply_coords(self) -> None:
        """Apply a completed CASET/RASET coordinate group to the window."""
        c = self._coords
        if len(c) < 4:
            return
        start = (c[0] << 8) | c[1]
        end = (c[2] << 8) | c[3]
        if self._cmd == 0x2A:      # CASET — column (x) range
            self._win_x = (start, end)
        elif self._cmd == 0x2B:    # RASET — row (y) range
            self._win_y = (start, end)
        x0, x1 = self._win_x
        y0, y1 = self._win_y
        self.fb.set_window(x0, y0, x1, y1)
        self._windows += 1
        if os.environ.get("BPV5_LCD_DEBUG"):
            print(f"[LcdFb] {('CASET' if self._cmd==0x2A else 'RASET')} -> "
                  f"window ({x0},{y0})-({x1},{y1}) "
                  f"[{x1-x0+1}x{y1-y0+1}] after {self._push_calls} px",
                  flush=True)

    # --- bring-up no-ops (replace skip_lcd_*) ---------------------------
    @bp_handler(["init"])
    def init(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        print("[LcdFb] lcd_init (modeled no-op)", flush=True)
        return True, 0

    @bp_handler(["configure"])
    def configure(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        return True, 0

    @bp_handler(["backlight"])
    def backlight(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        return True, 0

    @bp_handler(["run_real"])
    def run_real(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Pass-through: let the hooked function execute for real (returns
        ``(False, ...)``). The overlay config uses this to OVERRIDE the default
        config's ``skip_lcd_write_string`` / ``skip_lcd_update`` so the real
        glyph rasterizer runs and streams pixels to the framebuffer."""
        return False, 0

    # --- snapshot trigger ------------------------------------------------
    @bp_handler(["dump"])
    def dump(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """Optional hook (e.g. ``ui_lcd_update`` return) to snapshot the PNG.

        Lets the hooked function RUN (returns ``(False, ...)``) so the screen
        keeps updating — we just snapshot the current framebuffer first.
        """
        self._dump()
        return False, 0

    def _dump(self) -> None:
        try:
            self.fb.save_png(self.png_path)
            print(f"[LcdFb] dumped {self.png_path} "
                  f"({self.fb.width}x{self.fb.height}, "
                  f"{self.fb.count_non_bg()} non-bg px, "
                  f"{self._push_calls} pixels, {self._windows} windows)",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[LcdFb] PNG dump failed: {e}", flush=True)
