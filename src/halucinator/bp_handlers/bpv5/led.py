# Copyright 2026 Christopher Wright

"""Bus Pirate v5 (RP2040) — modeled addressable-LED strip sink (WS2812/APA102).

LED mode (menu #10) is **output-only**: the firmware streams pixel words to a
PIO state machine that bit-bangs the WS2812 (single-wire) or APA102
(clock+data) waveform. There is no target device to answer reads — "the
interface works" is proved by **capturing the exact pixel/frame words the
firmware emits** and showing they carry the colour the user drove through the
CLI.

Per ``INTERFACES.md``, LED is PIO-bit-banged, so we do
**high-level emulation at the software leaf helpers** instead of emulating the
PIO TX FIFO:

* ``ws2812_write(uint32_t v)``  — the WS2812 per-pixel write. The colour word
  arrives in **r0** (``get_arg(0)``). The firmware applies ``v << 8`` and
  pushes it to the PIO TX FIFO, where a 4-instruction PIO program shifts out
  the **top 24 bits, MSB-first**. So the meaningful 24 bits of ``v`` are the
  on-wire bytes, in order ``[v>>16, v>>8, v>>0]``. The Bus Pirate firmware
  does **no RGB→GRB reorder** — the user supplies the value already in the
  strip's wire order, which for WS2812 is **G, R, B**. Thus a CLI write of
  ``0xGGRRBB`` lights one pixel with green=GG, red=RR, blue=BB.
* ``apa102_write(uint32_t v)`` — the APA102 per-pixel write. ``v`` (r0) is
  pushed to the FIFO **verbatim** (no shift); the 32-bit APA102 LED frame is
  ``0xE?BBGGRR``-style as the user composed it.
* ``hwled_write(ctx)``         — the mode-vtable write entry. It loads the
  parsed CLI value from ``[ctx+0x14]`` into r0 and dispatches through the
  per-strip op-table to ``ws2812_write``/``apa102_write``. We hook it too, to
  read the colour word straight from the command struct (a cross-check that
  matches the leaf-helper capture) and to know which strip type is active.
* ``hwled_start`` / ``hwled_stop`` — the CLI ``[`` / ``]`` transaction frame.
  We use them purely as frame delimiters for the capture log.

The ABI (Thumb, base 0x10000000) was RE'd from ``bus_pirate5_rev10.bin``:
``ws2812_write`` @ 0x10020090 (``lsls r0,#8`` then ``str`` to FIFO),
``apa102_write`` @ 0x100200fc (direct ``str``), ``hwled_write`` @ 0x10020728
(``ldr r0,[r0,#0x14]`` then ``blx`` op-table+0x10), ctx struct @ 0x20039944
(strip-type index @ +4, colour word @ +0x14).

Nothing is returned to the firmware that matters (these are ``void`` writes),
so each hook returns ``(True, 0)`` to skip the real PIO FIFO write and log the
captured word. ``skip_hwled_setup_exc`` (in the config) bypasses the per-strip
PIO program load / SM config so setup doesn't touch unmodeled PIO MMIO — safe
because we never dereference the PIO config it would have built.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend


# Strip-type index stored at ctx+4 (0-based: prompt "1/2/3" -> index 0/1/2).
_STRIP_NAMES = {0: "WS2812", 1: "APA102", 2: "ONBOARD"}


class LedStripSink(BPHandler):
    """Captures every pixel word the firmware emits in LED mode.

    Output-only: there is no device to answer reads. We log the on-wire bytes
    each ``ws2812_write`` / ``apa102_write`` emits so a test can assert the
    exact colour frame the firmware drove through the CLI.
    """

    # ctx struct holding the live LED settings (strip-type index @ +4).
    CTX_ADDR = 0x20039944
    CTX_TYPE_OFF = 0x04
    CTX_COLOR_OFF = 0x14

    def __init__(self) -> None:
        super().__init__()
        self._reset_frame()
        # All pixel words captured across the run, for end-of-run summaries.
        self._all: List[Tuple[str, int]] = []
        print("[LedStripSink] modeled addressable-LED sink attached "
              "(output-only: capturing emitted pixel/frame bytes)", flush=True)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _reset_frame(self) -> None:
        self._frame: List[Tuple[str, int]] = []

    def _strip_index(self, qemu: "HalBackend") -> Optional[int]:
        try:
            raw = qemu.read_memory(self.CTX_ADDR + self.CTX_TYPE_OFF, 4)
            if isinstance(raw, (bytes, bytearray)):
                return int.from_bytes(raw, "little") & 0xFF
            return int(raw) & 0xFF
        except Exception:
            return None

    def _strip_name(self, qemu: "HalBackend") -> str:
        idx = self._strip_index(qemu)
        return _STRIP_NAMES.get(idx, f"type{idx}") if idx is not None else "?"

    @staticmethod
    def _ws2812_wire_bytes(v: int) -> Tuple[int, int, int]:
        """WS2812 on-wire bytes (the top 24 bits of ``v``, MSB-first = G,R,B).

        The firmware does ``v << 8`` then the PIO shifts the top 24 bits out
        MSB-first, so the emitted bytes are ``v[23:16] v[15:8] v[7:0]`` — the
        WS2812 wire order G, R, B.
        """
        g = (v >> 16) & 0xFF
        r = (v >> 8) & 0xFF
        b = v & 0xFF
        return g, r, b

    # ------------------------------------------------------------------ #
    # transaction framing (CLI '[' / ']') — capture delimiters only
    # ------------------------------------------------------------------ #
    @bp_handler(["start"])
    def start(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwled_start(cmd)`` — CLI ``[``. Begin a capture frame.

        We OBSERVE (``return False, 0``) so the real ``hwled_start`` still
        runs and stores its render token (the "RESET" start label) into
        ``cmd[+8]`` for the firmware's CLI transaction echo. The PIO-touching
        per-strip ``*_start`` op it dispatches to is neutralised separately by
        a ``SkipFunc`` on ``ws2812_start``/``apa102_start`` (the PIO bring-up
        is bypassed, so that op would otherwise poke a zeroed PIO SM and spin).
        """
        self._reset_frame()
        print(f"[LedStripSink] FRAME START (strip={self._strip_name(qemu)})",
              flush=True)
        return False, 0

    @bp_handler(["stop"])
    def stop(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwled_stop(cmd)`` — CLI ``]``. End and summarise the frame.

        Observed (``return False, 0``) like ``start`` so the firmware stores
        and renders the stop label; the per-strip ``*_stop`` PIO op is
        ``SkipFunc``'d separately.
        """
        n = len(self._frame)
        if n:
            body = "  ".join(
                "%s=0x%06X" % (s, v) for s, v in self._frame
            )
            print(f"[LedStripSink] FRAME END ({n} pixel(s)): {body}", flush=True)
        else:
            print("[LedStripSink] FRAME END (0 pixels)", flush=True)
        return False, 0

    # ------------------------------------------------------------------ #
    # mode-vtable write entry — reads the parsed colour from the cmd struct
    # ------------------------------------------------------------------ #
    @bp_handler(["hwled_write"])
    def hwled_write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``hwled_write(ctx)`` — the CLI bare-value write dispatcher.

        ``ctx`` is r0; the parsed colour word is at ``[ctx+0x14]``. We log it
        (a cross-check of the leaf-helper capture) but return ``None`` so the
        real dispatch into ``ws2812_write``/``apa102_write`` still happens and
        the leaf hook below performs the authoritative capture.
        """
        ctx = qemu.get_arg(0)
        try:
            raw = qemu.read_memory(ctx + self.CTX_COLOR_OFF, 4)
            color = (int.from_bytes(raw, "little")
                     if isinstance(raw, (bytes, bytearray)) else int(raw))
        except Exception:
            return False, 0
        print(f"[LedStripSink] hwled_write: cmd[+0x14]=0x{color & 0xFFFFFFFF:08X} "
              f"(strip={self._strip_name(qemu)})", flush=True)
        return False, 0

    # ------------------------------------------------------------------ #
    # WS2812 leaf helper — authoritative per-pixel capture
    # ------------------------------------------------------------------ #
    @bp_handler(["ws2812_write"])
    def ws2812_write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void ws2812_write(uint32_t v)`` — emit one WS2812 pixel.

        The colour word is r0 (``get_arg(0)``). We capture it, decode the
        G/R/B wire bytes, and **skip** the real FIFO write (return ``(True,
        0)``) so nothing touches the unmodeled PIO MMIO.
        """
        v = qemu.get_arg(0) & 0xFFFFFFFF
        g, r, b = self._ws2812_wire_bytes(v)
        rec = ("WS2812", v & 0xFFFFFF)
        self._frame.append(rec)
        self._all.append(rec)
        print(
            "[LedStripSink] WS2812 PIXEL word=0x%06X -> wire bytes "
            "G=0x%02X R=0x%02X B=0x%02X  (decoded R=0x%02X G=0x%02X B=0x%02X)"
            % (v & 0xFFFFFF, g, r, b, r, g, b),
            flush=True,
        )
        return True, 0

    # ------------------------------------------------------------------ #
    # APA102 leaf helper — authoritative per-pixel capture
    # ------------------------------------------------------------------ #
    @bp_handler(["apa102_write"])
    def apa102_write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        """``void apa102_write(uint32_t v)`` — emit one APA102 32-bit frame.

        ``v`` (r0) is pushed to the FIFO verbatim (no shift). We capture the
        full 32-bit frame word and skip the real write.
        """
        v = qemu.get_arg(0) & 0xFFFFFFFF
        rec = ("APA102", v)
        self._frame.append(rec)
        self._all.append(rec)
        print(
            "[LedStripSink] APA102 FRAME word=0x%08X "
            "(bytes %02X %02X %02X %02X)"
            % (v, (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF),
            flush=True,
        )
        return True, 0
