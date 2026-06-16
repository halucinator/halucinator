# Copyright 2026 Christopher Wright

"""bpv5-local printf handler.

Subclasses the generic ``Libc6`` and overrides ONLY ``printf`` with handling
the Bus Pirate v5 firmware needs but that doesn't belong in the shared,
arch-agnostic ``Libc6`` (which serves MIPS/PPC/x86 too and whose unit-test
contract models args as abstract values, not ARM register words):

  * ``%c`` reads the argument AS A CHARACTER CODE (``chr(arg & 0xFF)``). The
    generic handler treats it as a string pointer, which crashes on a real
    char code — the bpv5 SPI driver prints ``TX: %c``-style bytes.
  * ``%f``/double is decoded per the **ARM AAPCS** soft-float convention: the
    firmware passes ``__aeabi_f2d`` results as a 64-bit double in an 8-byte
    aligned core-register pair (r2:r3 for ``printf("...%f", d)``); we walk the
    variadic words, honour the alignment padding, and reinterpret the two
    words' raw bits as IEEE-754. Used by the DS18B20 temperature + NMEA demos.
  * width/precision (``%02X``) and C length modifiers (``%08lx``) are parsed
    and the length modifier stripped so Python's ``%`` accepts the spec.

Everything else (``_fmt_idx`` / ``fmt_idx`` registration, ``_publish_stdio``
UTTY bridging, ``puts``) is inherited unchanged from ``Libc6``. Wired in
``bpv5_config.yaml`` for ``SEGGER_RTT_printf`` and ``printf_``.
"""
from __future__ import annotations

import re
import struct
from typing import TYPE_CHECKING, Tuple, cast

from halucinator import hal_log as hal_logging
from halucinator.bp_handlers.bp_handler import HandlerFunction, bp_handler
from halucinator.bp_handlers.generic.libc import Libc6

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

hal_log = hal_logging.getHalLogger()

_SPEC_RE = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?([hlLjzt]*)([diouxXeEfFgGaAcsp%])")


class Bpv5Printf(Libc6):
    """``Libc6`` with ARM-AAPCS ``%f``/``%c`` printf handling for bpv5."""

    def register_handler(self, qemu: "HalBackend", addr: int, func_name: str,
                         fmt_idx: int = 0) -> HandlerFunction:
        """Same as Libc6.register_handler, but bind OUR printf override.
        Libc6's version hardcodes ``Libc6.printf``, so without this the base
        parser would run even though the config names Bpv5Printf."""
        self._fmt_idx[addr] = fmt_idx
        return cast(HandlerFunction, Bpv5Printf.printf)

    @bp_handler(["printf"])
    def printf(self, qemu: "HalBackend", bp_addr: int) -> Tuple[bool, int]:  # noqa: C901
        """``int printf(const char *format, ...)`` — ARM-AAPCS aware."""
        idx = self._fmt_idx.get(bp_addr, 0)
        fmt = qemu.read_string(qemu.get_arg(idx))

        printf_args = []
        out_fmt_parts = []
        last = 0
        # Variadic words past the format string. slot S maps to get_arg(idx+1+S).
        arg_slot = 0

        def _word(slot: int) -> int:
            return int(qemu.get_arg(idx + 1 + slot)) & 0xFFFFFFFF

        for m in _SPEC_RE.finditer(fmt):
            out_fmt_parts.append(fmt[last:m.start()])
            last = m.end()
            type_char = m.group(2)
            if type_char == "%":
                out_fmt_parts.append("%%")
                continue
            spec = m.group(0)
            if m.group(1):                       # strip C length modifier
                spec = spec[:m.start(1) - m.start()] + type_char
            out_fmt_parts.append(spec)

            if type_char in "fFeEgGaA":          # AAPCS double in a reg pair
                if arg_slot % 2 == 0:            # 8-byte alignment padding
                    arg_slot += 1
                lo, hi = _word(arg_slot), _word(arg_slot + 1)
                arg_slot += 2
                printf_args.append(struct.unpack("<d", struct.pack("<II", lo, hi))[0])
                continue

            value = _word(arg_slot)
            arg_slot += 1
            if type_char in "diu":
                value = int(value)
                if type_char in "di" and value >= 0x80000000:
                    value -= 0x100000000
            elif type_char in "ouxX":
                value = int(value) & 0xFFFFFFFF
            elif type_char == "c":               # char: the arg IS the code
                value = chr(int(value) & 0xFF)
            elif type_char == "s":
                value = qemu.read_string(value)
            elif type_char == "p":
                out_fmt_parts[-1] = "0x%x"
                value = int(value) & 0xFFFFFFFF
            else:
                hal_log.warning("Unhandled printf format %%%s in %r", type_char, fmt)
                arg_slot -= 1
                out_fmt_parts[-1] = m.group(0)
                continue
            printf_args.append(value)
        out_fmt_parts.append(fmt[last:])

        try:
            print_string = "".join(out_fmt_parts) % tuple(printf_args)
        except (TypeError, ValueError):
            print_string = fmt                   # never crash the run
        if not self._publish_stdio(print_string):
            hal_log.info("%s", print_string)
        return True, len(print_string)
