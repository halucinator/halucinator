# Copyright 2022 GrammaTech Inc.
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, cast

from halucinator import hal_log as hal_log_conf
from halucinator.bp_handlers.bp_handler import (
    BPHandler,
    HandlerFunction,
    HandlerReturn,
    bp_handler,
)

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget

hal_log_conf.setLogConfig()
hal_logger = hal_log_conf.getHalLogger()


class DebugPrint(BPHandler):
    """Display a string passed to a function in the log output.

        Halucinator configuration usage:
        - class: halucinator.bp_handlers.generic.debug_print.DebugPrint
          function: <func_name> (Can be anything)
          addr: <addr>
          registration_args:{argument:1,
                             prefix:""} (Optional)
    """

    def __init__(self) -> None:
        self.argument: Dict[int, int] = {}
        self.prefix: Dict[int, str] = {}

    def register_handler(
        self,
        qemu: HALQemuTarget,
        addr: int,
        func_name: str,
        argument: int = 1,
        prefix: str = "",
    ) -> HandlerFunction:
        if argument > 4:
            raise ValueError("Argument limited to first four registers")
        self.qemu = qemu
        self.argument[addr] = argument
        self.prefix[addr] = prefix
        return cast(HandlerFunction, DebugPrint.output)

    @bp_handler
    def output(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """Displays the text of the message.
        """
        pfxstr = self.prefix[addr]
        # Find the base address of the string we will output
        # For the user's convenience, we number arguments from 1
        # and convert here to QEMU's zero-based notation
        strbase = qemu.get_arg(self.argument[addr] - 1)
        try:
            # This does not try to do varargs processing!
            # Casting is required here because while we know that 'raw=True'
            # gives us back a byte array, the subsequent find, decode,
            # and indexing need to know they're legal.
            dbgstr = cast(
                bytes, self.qemu.read_memory(strbase, 1, 80, raw=True)
            )
            term = dbgstr.find(b"\x00")
            if term >= 0:
                dbgstr = dbgstr[:term]
            hal_logger.info(pfxstr + dbgstr.decode("utf-8"))
        except Exception as err:
            hal_logger.exception(err)
        return True, 0
