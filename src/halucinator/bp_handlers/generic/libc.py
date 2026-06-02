"""Libc function break points"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar, Dict, Optional, Tuple, cast

from halucinator import hal_log as hal_logging
from halucinator.bp_handlers.bp_handler import BPHandler, HandlerFunction, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hal_log = hal_logging.getHalLogger()


class Libc6(BPHandler):
    """This class holds generic libc functionality, such as printf and puts.

    By default ``puts`` / ``printf`` output is mirrored to a UTTY interface
    named in ``_STDIO_INTERFACE_ID`` so external devices that subscribe to
    ``Peripheral.UTTYModel.tx_buf`` (e.g. ``hal_dev_uart``, the bpv5
    terminal device) can display firmware stdio on a real terminal —
    matching how the STM32 UART example wires the UART HAL through a
    peripheral model rather than printing directly inside halucinator.

    Set ``_STDIO_INTERFACE_ID = None`` in a subclass (or via configuration)
    to fall back to plain ``print()`` if no external device is involved.

    For ``printf``-family functions whose format-string argument isn't at
    position 0 — ``SEGGER_RTT_printf(channel, fmt, ...)`` puts fmt at 1,
    ``snprintf(buf, size, fmt, ...)`` puts it at 2, fprintf/dprintf at 1,
    custom logging macros at whatever the wrapper packs in front — set
    ``registration_args.fmt_idx`` per intercept in YAML::

        - class: halucinator.bp_handlers.generic.libc.Libc6
          function: printf
          symbol: SEGGER_RTT_printf
          registration_args:
            fmt_idx: 1
    """

    #: Name of the UTTY interface that printf/puts output is published to.
    #: When ``None``, the legacy ``print()`` path is used instead.
    _STDIO_INTERFACE_ID: ClassVar[Optional[str]] = "STDIO"

    def __init__(self) -> None:
        super().__init__()
        # Per-intercept-address override for the format-string argument
        # position, populated by register_handler() when the YAML sets
        # registration_args.fmt_idx. Unset entries default to 0 (standard
        # C printf).
        self._fmt_idx: Dict[int, int] = {}
        self._stdio_iface: Optional[str] = self._register_stdio_interface()

    def register_handler(self, qemu: "HalBackend", addr: int, func_name: str,
                         fmt_idx: int = 0) -> HandlerFunction:  # pylint: disable=unused-argument
        """Record the per-address ``fmt_idx`` so ``printf`` can find the
        format string at the right argument position for *this* intercept."""
        self._fmt_idx[addr] = fmt_idx
        return cast(HandlerFunction, Libc6.printf)

    @classmethod
    def _register_stdio_interface(cls) -> Optional[str]:
        """Best-effort: register the STDIO UTTY interface on import.

        Returns the interface id on success, or None if UTTYModel isn't
        importable / the interface couldn't be registered (in which case
        printf falls back to plain ``print()``).
        """
        iface = cls._STDIO_INTERFACE_ID
        if iface is None:
            return None
        try:
            from halucinator.peripheral_models.utty import UTTYModel  # noqa: WPS433
            UTTYModel.add_interface(iface, enabled=True)
            return iface
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            log.debug("Libc6: STDIO UTTY interface registration skipped (%s)", exc)
            return None

    def _publish_stdio(self, text: str) -> bool:
        """Publish ``text`` via ``Peripheral.UTTYModel.tx_buf``.

        Returns True on success. False means the caller should fall back
        to ``print()`` for visibility.
        """
        if self._stdio_iface is None:
            return False
        try:
            from halucinator.peripheral_models.utty import UTTYModel  # noqa: WPS433
            UTTYModel.tx_buf(self._stdio_iface,
                             text.encode("utf-8", errors="replace"))
            return True
        except Exception:  # noqa: BLE001
            return False

    @bp_handler(["puts"])
    def puts(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """int puts(const char *str)"""
        hal_log.debug("puts 0x%08x", addr)
        print_string = qemu.read_string(qemu.get_arg(0))
        # puts appends a newline; mirror that for downstream consumers.
        if not self._publish_stdio(print_string + "\n"):
            hal_log.info("%s", print_string)
        return True, 1

    @bp_handler(["printf"])
    def printf(self, qemu: "HalBackend", bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=no-self-use,unused-argument
        """int printf(const char *format, ...)
        handles most formats, but we don't do anything special
        for length or for the n. The n should never have been created"""
        # pylint: disable=too-many-branches
        idx = self._fmt_idx.get(bp_addr, 0)
        fmt = qemu.read_string(qemu.get_arg(idx))

        formats = []  # We aren't handling anything fancy or even length args
        strsplit = re.split(r"(\W)", fmt)
        for i, element in enumerate(strsplit):
            if element == "%" and len(strsplit) > i + 1:
                if i >= 1 and strsplit[i - 1] != "\\":
                    formats.append(strsplit[i + 1])
        printf_args = []
        for i, form in enumerate(formats):
            arg_int = idx + i + 1
            value = qemu.get_arg(arg_int)
            if "i" in form or "d" in form or "u" in form:  # int
                value = int(value)
            elif "f" in form or "F" in form:  # double in normal form
                value = float(value)
            elif "x" in form or "X" in form:  # hexidecimal
                value = int(value)
            elif "s" in form:  # null terminated string
                value = qemu.read_string(value)
            elif "c" in form:  # character
                value = qemu.read_string(value)[0]
            elif "e" in form or "E" in form:  # double in standard form
                value = float(value)
            elif "g" in form or "G" in form:  # double in normal or exponential form
                value = float(value)
            elif "o" in form:  # unsigned int in octal
                value = int(value)
            elif "a" in form or "A" in form:  # double in dex notation
                value = float(value)
            # elif "p" in form: #void pointer
            #     print("Void pointer")
            # # elif "n" in form:
            # # print nothing but writes the number of characters written so
            #   far into integer pointer parameter
            else:
                hal_log.warning("Unhandled printf format %%%s in %r", form, fmt)
                return True, 1
            printf_args.append(value)

        print_string = fmt % tuple(printf_args)
        if not self._publish_stdio(print_string):
            hal_log.info("%s", print_string)
        return True, len(print_string)

    @bp_handler(["exit"])
    def halucinator_exit(
        self, qemu: "HalBackend", addr: int
    ) -> Tuple[bool, None]:  # pylint: disable=no-self-use,unused-argument
        """
        Exits Halucinator when exit is called returning the
        status code exit was called with
        """
        ret_value = qemu.get_arg(0) & 0xFF
        qemu.halucinator_shutdown(ret_value)
        return False, None