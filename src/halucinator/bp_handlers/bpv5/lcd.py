# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — ST7789 LCD model (draw-op capture).

The Bus Pirate v5 carries a 320x240 ST7789 TFT on the **shared** PL022 SSP
(``0x40040000``) plus a few GPIOs. The stock rehost bypassed the whole LCD
bring-up with ``skip_lcd_*`` SkipFuncs, so nothing the firmware "draws" was
ever observable — the display was a black hole.

This module replaces those spoofs with a **real model** that CAPTURES the
firmware's draw operations at the text/draw helper seam (HLE), proving the
firmware actually renders text. We do *not* model the ST7789 command/data
pixel pipeline over SPI — that would be heavy and, worse, would contend with
the already-working SPI keystone on the same SSP. Instead we intercept the
firmware's software draw helpers, which are distinct symbols from the
``hwspi_*`` leaves, so the SPI flash model is untouched.

RE'd ABI (capstone Thumb, flash base 0x10000000):

* ``lcd_write_string(r0=font_desc*, r1=fg_color, r2=bg_color, r3=char* str,
  [sp+0]=attr16)`` — r3 is a NUL-terminated C string. The function then walks
  the string glyph-by-glyph and blits over SPI. We read r3 as the drawn text.
  x/y are NOT register args here.
* ``lcd_set_bounding_box(r0=x_start, r1=y_start, r2=x_end/w, r3=y_end/h)`` —
  the cursor window is programmed into the controller out-of-band *before*
  the string is drawn (ST7789 cmds 0x2a/0x2b). We track the most-recent box
  so each captured string can be annotated with its on-screen rectangle.

Both intercepts return ``(True, 0)`` so the real (SPI-driving) body is
skipped — the capture is the deliverable, and skipping keeps the shared SSP
free of LCD traffic so the SPI flash keystone keeps passing.

The firmware draws a fixed channel-label row at idle —
``"Vout","IO0".."IO7","GND"`` (via ``ui_lcd_update``) — plus runtime voltage
values. "Vout" is a stable, statically-known string and serves as the proof
that the firmware really drew text through this model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


class St7789Lcd(BPHandler):
    """Modeled ST7789 TFT that captures the firmware's text draw operations.

    Hooks (wired in ``bpv5_config.yaml``):

    * ``write_string`` -> ``lcd_write_string`` — capture the drawn C string
      (r3) plus fg/bg colors (r1/r2) and the last bounding box.
    * ``set_bounding_box`` -> ``lcd_set_bounding_box`` — track the window
      rectangle so each string is annotated with x/y.
    * ``init`` / ``configure`` / ``backlight`` — no-op stand-ins that replace
      the old ``skip_lcd_*`` SkipFuncs (the panel needs no real bring-up for
      capture, and these keep the boot path off the shared SSP).

    Every captured string is logged as ``[Lcd] write_string("...", x, y)`` and
    accumulated into ``self.drawn`` so a test can assert expected on-screen
    text appeared.
    """

    def __init__(self) -> None:
        super().__init__()
        # Last window programmed via lcd_set_bounding_box (x, y, x_end, y_end).
        self._box: Tuple[int, int, int, int] = (0, 0, 0, 0)
        # Every string the firmware drew, in order (text, x, y, fg, bg).
        self.drawn: List[Tuple[str, int, int, int, int]] = []
        print("[Lcd] modeled ST7789 320x240 TFT attached "
              "(capturing draw operations)", flush=True)

    # --- window / cursor -------------------------------------------------
    @bp_handler(["set_bounding_box"])
    def set_bounding_box(self, qemu: "HalBackend",
                         addr: int) -> Tuple[bool, int]:
        """``lcd_set_bounding_box(x, y, x_end, y_end)`` — record the window."""
        x = qemu.get_arg(0) & 0xFFFF
        y = qemu.get_arg(1) & 0xFFFF
        xe = qemu.get_arg(2) & 0xFFFF
        ye = qemu.get_arg(3) & 0xFFFF
        self._box = (x, y, xe, ye)
        return True, 0

    # --- text draw -------------------------------------------------------
    @bp_handler(["write_string"])
    def write_string(self, qemu: "HalBackend",
                     addr: int) -> Tuple[bool, int]:
        """``lcd_write_string(font*, fg, bg, char* str, attr)`` — capture."""
        fg = qemu.get_arg(1) & 0xFFFF
        bg = qemu.get_arg(2) & 0xFFFF
        str_ptr = qemu.get_arg(3)
        text = self._read_cstr(qemu, str_ptr)
        x, y = self._box[0], self._box[1]
        self.drawn.append((text, x, y, fg, bg))
        print(f'[Lcd] write_string("{text}", x={x}, y={y}, '
              f'fg=0x{fg:04x}, bg=0x{bg:04x})', flush=True)
        return True, 0

    # --- bring-up no-ops (replace skip_lcd_*) ---------------------------
    @bp_handler(["init"])
    def init(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        print("[Lcd] lcd_init (modeled no-op)", flush=True)
        return True, 0

    @bp_handler(["configure"])
    def configure(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        return True, 0

    @bp_handler(["backlight"])
    def backlight(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        return True, 0

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _read_cstr(qemu: "HalBackend", ptr: int, max_len: int = 128) -> str:
        """Read a NUL-terminated string, tolerating a bad pointer."""
        if not ptr:
            return ""
        try:
            return qemu.read_string(ptr, max_len)
        except Exception:  # noqa: BLE001 — never let a bad ptr crash the run
            return ""
