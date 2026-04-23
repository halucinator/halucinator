# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.
from __future__ import annotations

"""
Implements the breakpoint handlers for common functions
"""

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from halucinator.peripheral_models import canary
from halucinator.bp_handlers.bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()


class SleepTime(BPHandler):
    """
    Break point handler that sleeps for a configured amount of time

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.SleepTime
      function: <func_name> (Can be anything)
      registration_args: {sleep_time: 10}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.sleep_times: Dict[int, int] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, sleep_time: int = 10
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.sleep_times[addr] = sleep_time
        return cast(HandlerFunction, SleepTime.sleep_time)

    @bp_handler
    def sleep_time(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Sleep for the configured amount of time and return 0
        """
        time.sleep(self.sleep_times[addr])
        return False, 0


class ReturnZero(BPHandler):
    """
    Break point handler that just returns zero

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.ReturnZero
      function: <func_name> (Can be anything)
      registration_args: {silent:false}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        return cast(HandlerFunction, ReturnZero.return_zero)

    @bp_handler
    def return_zero(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return 0
        """
        if not self.silent[addr]:
            hal_log.info("ReturnZero: %s ", self.func_names[addr])
        return True, 0


class ReturnConstant(BPHandler):
    """
    Break point handler that returns a constant

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.ReturnConstant
      function: <func_name> (Can be anything)
      registration_args: { ret_value:(value), silent:false}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.ret_values: Dict[int, Optional[int]] = {}
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, ret_value: Optional[int] = None, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument, too-many-arguments
        self.ret_values[addr] = ret_value
        self.silent[addr] = ret_value
        self.func_names[addr] = func_name
        return cast(HandlerFunction, ReturnConstant.return_constant)

    @bp_handler
    def return_constant(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return constant
        """
        if not self.silent[addr]:
            hal_log.info(
                "ReturnConstant: %s : %#x", self.func_names[addr], self.ret_values[addr]
            )
        return True, self.ret_values[addr]


class Canary(BPHandler):
    """
    Break point handler for handling canaries

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.Canary
      function: <func_name> (Can be anything)
      registration_args: { canary_type:(VALUE), msg:(VALUE) }
      addr: <addr>
    """

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}
        self.canary_type: Dict[int, Optional[str]] = {}
        self.msg: Dict[int, str] = {}
        self.model = canary.CanaryModel

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, canary_type: Optional[str] = None, msg: str = ""
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.func_names[addr] = func_name
        self.canary_type[addr] = canary_type
        self.msg[addr] = msg
        return cast(HandlerFunction, Canary.handle_canary)

    @bp_handler
    def handle_canary(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """
        Call the peripheral model
        """
        hal_log.critical(
            "%s Canary intercepted in %s: %s ",
            self.canary_type[addr],
            self.func_names[addr],
            self.msg[addr],
        )
        self.model.canary(qemu, addr, self.canary_type[addr], self.msg[addr])
        return True, 0


class PrintChar(BPHandler):
    """
    Break point handler that immediately returns from the function

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.SkipFunc
      function: <func_name> (Can be anything)
      registration_args: {silent:false}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}
        self.intercept: Dict[int, bool] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, silent: bool = False, intercept: bool = True
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        """
        register the put_char method to handle all BP's
        for this class.

        :param silent:  Turns on and printing to the HAL_log when
        the put_char handler executes
        """
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.intercept[addr] = intercept

        return cast(HandlerFunction, PrintChar.put_char)

    @bp_handler
    def put_char(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """
        Just return
        """
        input_char = chr(qemu.get_arg(0))
        ret_addr = qemu.get_ret_addr()
        if not self.silent[addr]:
            hal_log.info(
                "%s (lr=0x%08x): %s ", self.func_names[addr], ret_addr, input_char
            )
        if self.intercept[addr]:
            return True, None
        return False, None


class PrintString(BPHandler):
    """
    Break point handler that prints the string with char * in arg N
    specified in registartion_args (Default N=0)

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.PrintString
      function: <func_name> (Can be anything)
      registration_args: {arg_num:0, silent:false}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}
        self.arg_num: Dict[int, int] = {}
        self.max_len: Dict[int, int] = {}
        self.intercept: Dict[int, bool] = {}

    def register_handler(
        self,
        qemu: HALQemuTarget,
        addr: int,
        func_name: str,
        arg_num: int = 0,
        max_len: int = 256,
        silent: bool = False,
        intercept: bool = True,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        """
        register the put_char method to handle all BP's
        for this class.

        :param silent:  Turns on and printing to the HAL_log when
        the put_char handler executes
        """
        self.arg_num[addr] = arg_num
        self.max_len[addr] = max_len
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.intercept[addr] = intercept
        return cast(HandlerFunction, PrintString.print_string)

    @bp_handler
    def print_string(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """
        Just return
        """
        if not self.silent[addr]:
            chr_ptr = qemu.get_arg(self.arg_num[addr])
            input_string = qemu.read_string(chr_ptr, self.max_len[addr])
            ret_addr = qemu.get_ret_addr()
            hal_log.info(
                "%s (0x%08x): %s", self.func_names[addr], ret_addr, input_string
            )

        if self.intercept[addr]:
            return True, None
        return False, None


class SkipFunc(BPHandler):
    """
    Break point handler that immediately returns from the function

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.SkipFunc
      function: <func_name> (Can be anything)
      registration_args: {silent:false}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        return cast(HandlerFunction, SkipFunc.skip)

    @bp_handler
    def skip(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Just return
        """
        if not self.silent[addr]:
            hal_log.info("SkipFunc: %s ", self.func_names[addr])
        return True, None


class MovePC(BPHandler):
    """
    Break point handler that just increments the PC to skip executing instructions

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.MovePC
      function: <func_name> (Can be anything)
      registration_args: {move_by: <int:4>, silent: <bool:False}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}
        self.move_pc_amount: Dict[int, int] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, move_by: int = 4, silent: bool = True
    ) -> HandlerFunction:  # pylint: disable=unused-argument,too-many-arguments
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.move_pc_amount[addr] = move_by
        return cast(HandlerFunction, MovePC.move_pc)

    @bp_handler
    def move_pc(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """
        Just return
        """
        pc = qemu.regs.pc
        move_by = self.move_pc_amount[addr]
        if not self.silent[addr]:
            hal_log.info(
                "MoveBy: %s moving from 0x%08x + 0x%08x (0x%08x)",
                self.func_names[addr],
                pc,
                move_by,
                pc + move_by,
            )
        qemu.regs.pc = pc + move_by
        return False, None


class KillExit(BPHandler):
    """
    Break point handler that stops emulation and kills avatar/halucinator

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.KillExit
      registration_args: {exit_code: <int>, silent: <bool:False>}
      function: <func_name> (Can be anything)
      addr: <addr>

    registration_args:
        exit_code:  Specifies the value sys.exit should be called with
        silent:     Controlls if print statements are made to hal_log
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.func_names: Dict[int, str] = {}
        self.exit_status: Dict[int, int] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, exit_code: int = 0, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument,too-many-arguments
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.exit_status[addr] = exit_code
        return cast(HandlerFunction, KillExit.kill_and_exit)

    @bp_handler
    def kill_and_exit(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        """
        Just return
        """
        if not self.silent[addr]:
            hal_log.info("Killing: %s ", self.func_names[addr])

        qemu.halucinator_shutdown(self.exit_status[addr])
        return False, None


class SetRegisters(BPHandler):
    """
    Break point handler that changes a register

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.SetRegisters
      function: <func_name> (Can be anything)
      registration_args: { registers: {'<reg_name>':<value>}}
      addr: <addr>
      addr_hook: True
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.changes: Dict[int, Dict[str, int]] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, registers: Dict[str, int] = {}, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument, dangerous-default-value
        self.silent[addr] = silent
        log.debug(
            "Registering: %s at addr: %s with SetRegisters %s",
            func_name,
            hex(addr),
            registers,
        )
        self.changes[addr] = registers
        return cast(HandlerFunction, SetRegisters.set_registers)

    @bp_handler
    def set_registers(self, qemu: HALQemuTarget, addr: int, *args: Any) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return 0
        """
        for change in self.changes[addr].items():
            reg = change[0]
            value = change[1]
            qemu.write_register(reg, value)
            log.debug("set_register: %s : %#x", reg, value)
        return False, 0


class SetMemory(BPHandler):
    """
    Break point handler that changes a memory address

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.SetMemory
      function: <func_name> (Can be anything)
      registration_args: { addresses: {<mem_address>: <value>}}
      addr: <addr>
    """

    def __init__(self) -> None:
        self.silent: Dict[int, bool] = {}
        self.changes: Dict[int, Dict[int, int]] = {}

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str, addresses: Dict[int, int] = {}, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument, dangerous-default-value
        self.silent[addr] = silent
        log.debug(
            "Registering: %s at addr: %s with SetMemory %s",
            func_name,
            hex(addr),
            addresses,
        )
        self.changes[addr] = addresses
        return cast(HandlerFunction, SetMemory.set_memory)

    @bp_handler
    def set_memory(self, qemu: HALQemuTarget, addr: int, *args: Any) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return 0
        """
        for change in self.changes[addr].items():
            address = change[0]
            value = change[1]
            qemu.write_memory(address, 4, value)
            log.debug("set_memory: %s : %#x", address, value)
        return False, 0
