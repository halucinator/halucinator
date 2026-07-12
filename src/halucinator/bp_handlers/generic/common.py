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
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()


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
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        return cast(HandlerFunction, ReturnZero.return_zero)

    @bp_handler
    def return_zero(self, qemu: "HalBackend", addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return 0
        """
        if not self.silent[addr]:
            hal_log.info("ReturnZero: %s ", self.func_names[addr])
        return True, 0


class ForceMemValue(BPHandler):
    """Write a value to a memory location, then let execution continue.

    Use to neutralise a dynamically-bound `_func_` hook pointer the firmware
    binds to a routine we can't satisfy: zero its global so the calling
    wrapper takes its null/return path (clean), instead of `mov pc, <hook>`
    into code that derefs an unconstructed context object and crashes.

    Config:
      - class: halucinator.bp_handlers.ForceMemValue
        class_args: {target_addr: 0x203d0638, value: 0, size: 4}
        function: <name>
        addr: <wrapper entry addr>
    """

    def __init__(self, target_addr: Optional[int] = None,
                 target_addrs: Optional[list] = None,
                 value: int = 0, size: int = 4) -> None:
        # Accept a single target_addr or a list target_addrs (zero a whole
        # set of stale _func_ globals at once).
        addrs = list(target_addrs) if target_addrs else []
        if target_addr is not None:
            addrs.append(target_addr)
        self.target_addrs = [int(a) for a in addrs]
        self.value = value
        self.size = size

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str, **kwargs: Any
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        return cast(HandlerFunction, ForceMemValue.force)

    @bp_handler
    def force(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        for t in self.target_addrs:
            try:
                qemu.write_memory(t, self.size, self.value)
                hal_log.info("ForceMemValue: [0x%08x]=0x%x", t, self.value)
            except Exception:  # noqa: BLE001
                log.exception("ForceMemValue write failed @ 0x%x", t)
        # Observe-and-continue: the real code runs against the new value(s).
        return False, None


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
        self, qemu: "HalBackend", addr: int, func_name: str, ret_value: Optional[int] = None, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument, too-many-arguments
        self.ret_values[addr] = ret_value
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        return cast(HandlerFunction, ReturnConstant.return_constant)

    @bp_handler
    def return_constant(self, qemu: "HalBackend", addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
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
        self, qemu: "HalBackend", addr: int, func_name: str, canary_type: Optional[str] = None, msg: str = ""
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.func_names[addr] = func_name
        self.canary_type[addr] = canary_type
        self.msg[addr] = msg
        return cast(HandlerFunction, Canary.handle_canary)

    @bp_handler
    def handle_canary(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
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
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = False, intercept: bool = True
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
    def put_char(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
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
        qemu: "HalBackend",
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
    def print_string(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
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
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        return cast(HandlerFunction, SkipFunc.skip)

    @bp_handler
    def skip(self, qemu: "HalBackend", addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Just return
        """
        if not self.silent[addr]:
            # Log SP/LR alongside so out-of-range SP corruption can be
            # localised to a specific SkipFunc point.
            try:
                sp = qemu.read_register("sp")
                lr = qemu.read_register("lr")
                hal_log.info("SkipFunc: %s  sp=0x%08x lr=0x%08x",
                             self.func_names[addr], sp, lr)
            except Exception:
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
        self, qemu: "HalBackend", addr: int, func_name: str, move_by: int = 4, silent: bool = True
    ) -> HandlerFunction:  # pylint: disable=unused-argument,too-many-arguments
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.move_pc_amount[addr] = move_by
        return cast(HandlerFunction, MovePC.move_pc)

    @bp_handler
    def move_pc(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
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
        self, qemu: "HalBackend", addr: int, func_name: str, exit_code: int = 0, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument,too-many-arguments
        self.silent[addr] = silent
        self.func_names[addr] = func_name
        self.exit_status[addr] = exit_code
        return cast(HandlerFunction, KillExit.kill_and_exit)

    @bp_handler
    def kill_and_exit(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
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
        self, qemu: "HalBackend", addr: int, func_name: str, registers: Dict[str, int] = {}, silent: bool = False
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
    def set_registers(self, qemu: "HalBackend", addr: int, *args: Any) -> HandlerReturn:  # pylint: disable=unused-argument
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
        self, qemu: "HalBackend", addr: int, func_name: str, addresses: Dict[int, int] = {}, silent: bool = False
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
    def set_memory(self, qemu: "HalBackend", addr: int, *args: Any) -> HandlerReturn:  # pylint: disable=unused-argument
        """
        Intercept Execution and return 0
        """
        for change in self.changes[addr].items():
            address = change[0]
            value = change[1]
            qemu.write_memory(address, 4, value)
            log.debug("set_memory: %s : %#x", address, value)
        return False, 0


class RegMemWrite(BPHandler):
    """
    Write a value to [register + offset], then let execution continue.

    For loop-/poll-breaking points where the address to poke is computed from a
    LIVE register (e.g. a `this`/`r4` pointer + a field offset) -- which
    SetMemory / ForceMemValue (fixed address only) cannot express. Used by the
    X-bus model to instantly ACK a remote I/O exchange ([r4+0x3e]=3 at the
    XbExchg::RemoteExchange ack-poll) and to mark a phantom I/O device absent
    ([r0+0x21c]=0xff at IoDeviceNoConf::StateMachine).

    Halucinator configuration usage:
    - class: halucinator.bp_handlers.RegMemWrite
      function: <func_name> (Can be anything)
      registration_args: { reg: r4, offset: 0x3e, value: 3, size: 1, silent: true }
      addr: <addr>
    """

    def __init__(self) -> None:
        self.params: Dict[int, Any] = {}

    def register_handler(  # pylint: disable=too-many-arguments
        self, qemu: "HalBackend", addr: int, func_name: str,
        reg: str = "r0", offset: int = 0, value: int = 0, size: int = 1, silent: bool = True
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.params[addr] = (reg, int(offset), int(value), int(size), silent, func_name)
        return cast(HandlerFunction, RegMemWrite.write)

    @bp_handler
    def write(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        reg, offset, value, size, silent, func_name = self.params[addr]
        base = qemu.read_register(reg)
        qemu.write_memory((base + offset) & 0xFFFFFFFF, size, value)
        if not silent:
            hal_log.info("RegMemWrite: %s [%s+0x%x]=0x%x", func_name, reg, offset, value)
        return False, None


class LogAndSkip(BPHandler):
    """Log entry (with r0..r3 + lr) then SkipFunc-return immediately.

    Equivalent of SkipFunc + LogAndContinue at the same address (which
    HALucinator's dispatcher doesn't support).  Returns r0=0 by default
    (override via `ret_value`).

    Designed for instrumenting C++ vfunc dispatch thunks: we want to
    SEE which slot/this-pointer is invoked, but if we let the thunk
    actually run it derails on uninit vptrs.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.LogAndSkip
        function: vfunc_slot_C
        addr: 0x20253088
        registration_args: { ret_value: 0, max_logs: 50 }
    """

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}
        self.n: Dict[int, int] = {}
        self.max_logs: Dict[int, int] = {}
        self.ret_value: Dict[int, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        ret_value: int = 0,
        max_logs: int = -1,
        silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        self.n[addr] = 0
        self.max_logs[addr] = max_logs
        self.ret_value[addr] = ret_value
        return cast(HandlerFunction, LogAndSkip.log_then_skip)

    @bp_handler
    def log_then_skip(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        self.n[addr] += 1
        max_n = self.max_logs[addr]
        if max_n < 0 or self.n[addr] <= max_n:
            try:
                lr = qemu.get_ret_addr()
            except Exception:
                lr = 0
            try:
                regs = {n: qemu.read_register(n) & 0xffffffff
                        for n in ("r0", "r1", "r2", "r3", "sp")}
                extra = "  r0=0x%(r0)08x r1=0x%(r1)08x r2=0x%(r2)08x r3=0x%(r3)08x sp=0x%(sp)08x" % regs
            except Exception:
                extra = ""
            hal_log.info("LogAndSkip: %s @ 0x%08x  (lr=0x%08x)%s",
                         self.func_names[addr], addr, lr, extra)
        # execute_return semantics: dispatcher sets r0 = ret_value, pc = lr
        return True, self.ret_value[addr]


class LogAndContinue(BPHandler):
    """Log that the function was entered, then let it run normally.

    Useful for tracing which functions a boot path actually reaches when
    diagnosing where a cold-start derails.  No registers or memory touched.

    With `max_logs` set, suppresses logging after that many fires (the BP
    still fires + continues, just silently).  Use this for observers
    planted on tight `b .` spin targets to avoid log floods.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.LogAndContinue
        function: <label>
        addr: <addr>
        registration_args: { max_logs: 4 }    # optional throttle
    """

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}
        self.n: Dict[int, int] = {}
        self.max_logs: Dict[int, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        max_logs: int = -1,
        silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        self.n[addr] = 0
        self.max_logs[addr] = max_logs
        return cast(HandlerFunction, LogAndContinue.log_then_continue)

    @bp_handler
    def log_then_continue(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        self.n[addr] += 1
        max_n = self.max_logs[addr]
        if max_n >= 0 and self.n[addr] > max_n:
            return False, None    # silent pass-through past the throttle
        try:
            lr = qemu.get_ret_addr()
        except Exception:
            lr = 0
        # Best-effort additional register dump for forensics.  Failure is
        # non-fatal (e.g., backend doesn't expose those registers).
        extra = ""
        try:
            regs = {n: qemu.read_register(n) & 0xffffffff
                    for n in ("r0", "r1", "r2", "r3", "sp")}
            extra = "  r0=0x%(r0)08x r1=0x%(r1)08x r2=0x%(r2)08x r3=0x%(r3)08x sp=0x%(sp)08x" % regs
        except Exception:
            pass
        hal_log.info("Reached: %s @ 0x%08x  (lr=0x%08x)%s",
                     self.func_names[addr], addr, lr, extra)
        return False, None  # continue executing the real function


class DumpMemory(BPHandler):
    """Dump a memory region when this address is reached.

    Useful to inspect kernel-set state at a known point (e.g. IRQ vector
    table after boot init, TCB structures after taskSpawn).

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.DumpMemory
        function: dump_vec_table
        addr: 0x201656fc                     # at reschedule entry
        class_args: {start: 0x0, length: 0x100, word_size: 4}
    """

    def __init__(self, start: int = 0, length: int = 0x80,
                 word_size: int = 4) -> None:
        # NOTE: class_args set via __init__ shared across all DumpMemory
        # instances (HALucinator class-level wiring); use per-address dicts
        # populated by register_handler so multiple DumpMemory entries can
        # each dump a different region.
        self.start = start
        self.length = length
        self.word_size = word_size
        self.func_names: Dict[int, str] = {}
        self.dumped: Dict[int, bool] = {}
        self.per_addr_start: Dict[int, int] = {}
        self.per_addr_length: Dict[int, int] = {}
        self.per_addr_word: Dict[int, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        start: Optional[int] = None,
        length: Optional[int] = None,
        word_size: Optional[int] = None,
        silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        self.dumped[addr] = False
        # Allow per-PC override via registration_args (preferred to class_args)
        self.per_addr_start[addr] = start if start is not None else self.start
        self.per_addr_length[addr] = length if length is not None else self.length
        self.per_addr_word[addr] = word_size if word_size is not None else self.word_size
        return cast(HandlerFunction, DumpMemory.dump_mem)

    @bp_handler
    def dump_mem(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        if self.dumped[addr]:
            return False, None  # only dump once per intercept addr
        self.dumped[addr] = True
        start = self.per_addr_start.get(addr, self.start)
        length = self.per_addr_length.get(addr, self.length)
        word_size = self.per_addr_word.get(addr, self.word_size)
        words = []
        for off in range(0, length, word_size):
            try:
                w = qemu.read_memory(start + off, word_size, 1)
                words.append(w & ((1 << (word_size * 8)) - 1))
            except Exception:
                words.append(None)
        hal_log.info("DumpMemory: %s @ 0x%08x  region 0x%08x..0x%08x:",
                     self.func_names[addr], addr, start,
                     start + length)
        for i in range(0, len(words), 4):
            row = words[i:i + 4]
            row_str = "  ".join(
                ("--------" if w is None else "%08x" % w) for w in row)
            hal_log.info("DumpMemory:   +%04x: %s",
                         i * word_size, row_str)
        return False, None  # continue


class FillMemory(BPHandler):
    """Write a fixed value to every word in a memory range, then continue.

    Useful for seeding a "fake object pool" where the firmware's
    uninitialised C++ object pointers can land. If the firmware then does
    object.vtable.method() through any offset, every load resolves back
    to the start of the pool, where a `bx lr` lives.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.FillMemory
        function: seed_fake_object
        addr: 0x20000184                  # at reset stub
        registration_args:
          start:      0x23ff8000          # start of fill region
          length:     0x400               # bytes to fill
          value:      0x23ff8000          # the 4-byte value to repeat
          entry_word: 0xe12fff1e          # value to put at `start` (bx lr)
    """

    def __init__(self) -> None:
        self.cfg: Dict[int, dict] = {}
        self.func_names: Dict[int, str] = {}
        self.done: Dict[int, bool] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        start: int = 0, length: int = 0, value: int = 0,
        entry_word: Optional[int] = None, silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.cfg[addr] = {
            "start": start, "length": length, "value": value,
            "entry_word": entry_word, "silent": silent,
        }
        self.func_names[addr] = func_name
        self.done[addr] = False
        return cast(HandlerFunction, FillMemory.fill_mem)

    @bp_handler
    def fill_mem(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        if self.done[addr]:
            return False, None  # only fill once per intercept addr
        self.done[addr] = True
        cfg = self.cfg[addr]
        start = cfg["start"]; length = cfg["length"]
        val = cfg["value"]
        # Write the repeating word.
        val_bytes = val.to_bytes(4, "little")
        n_words = length // 4
        payload = val_bytes * n_words
        # Optional special entry word at the very start (e.g. `bx lr`)
        if cfg["entry_word"] is not None:
            entry_bytes = cfg["entry_word"].to_bytes(4, "little")
            payload = entry_bytes + payload[4:]
        try:
            qemu.write_memory_bytes(start, payload)
        except Exception as _e:
            hal_log.error("FillMemory: write to 0x%x len %d failed: %s",
                          start, length, _e)
            return False, None
        if not cfg["silent"]:
            hal_log.info("FillMemory: %s @ 0x%08x  filled 0x%x..0x%x "
                         "with 0x%08x (entry=0x%08x)",
                         self.func_names[addr], addr, start,
                         start + length, val,
                         cfg["entry_word"] if cfg["entry_word"] is not None else val)
        return False, None  # continue


class CallFunction(BPHandler):
    """Make a manufactured firmware-function call from a trigger PC.

    Useful for kickstarting a kernel function whose natural call path
    is broken (e.g., calling taskSpawn directly because cold-boot
    derail meant the kernel never got to call it itself).

    On the FIRST fire at the configured trigger `addr`:
      * Sets r0..r3 from `args` (first 4 args per ARM AAPCS).
      * Pushes `stack_args` onto the stack (5th arg onwards live at
        [sp+0], [sp+4], ...).
      * Sets LR = `return_addr` (where the called function returns to).
      * Sets PC = `target` (the function to call).
    Then lets the dispatcher resume; the next emu_start lands at PC
    = target.

    On subsequent fires, no-op (passes through to the original
    instruction at the trigger PC).

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.CallFunction
        function: bootstrap_taskSpawn
        addr: 0x23ff7100                 # trigger (e.g., idle loop)
        registration_args:
          target:      0x2019d84c        # taskSpawn entry
          args:        [0x23ff5000, 100, 0, 0x2000]
          stack_args:  [0x23ff7200, 0]   # entryPt, arg1
          return_addr: 0x23ff7400        # LR; control returns here
    """

    def __init__(self) -> None:
        self.cfg: Dict[int, dict] = {}
        self.func_names: Dict[int, str] = {}
        self.fired: Dict[int, bool] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        target: int = 0,
        args: Optional[list] = None,
        stack_args: Optional[list] = None,
        return_addr: int = 0,
        sp_value: Optional[int] = None,
        silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.cfg[addr] = {
            "target": target,
            "args": list(args) if args else [],
            "stack_args": list(stack_args) if stack_args else [],
            "return_addr": return_addr,
            "sp_value": sp_value,
            "silent": silent,
        }
        self.func_names[addr] = func_name
        self.fired[addr] = False
        return cast(HandlerFunction, CallFunction.do_call)

    @bp_handler
    def do_call(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        if self.fired[addr]:
            return False, None    # pass through on subsequent hits
        self.fired[addr] = True
        cfg = self.cfg[addr]
        # Pin SP to a known-good location if requested. Used when the
        # bootstrap fires at a rescue PC where SP may point anywhere.
        if cfg["sp_value"] is not None:
            try:
                qemu.write_register("sp", cfg["sp_value"] & 0xffffffff)
            except Exception as _e:
                hal_log.error("CallFunction: sp pin failed: %s", _e)
        # Set r0..r3 (only what we have)
        regs = ["r0", "r1", "r2", "r3"]
        for i, v in enumerate(cfg["args"][:4]):
            try:
                qemu.write_register(regs[i], v & 0xffffffff)
            except Exception as _e:
                hal_log.error("CallFunction: set %s failed: %s", regs[i], _e)
        # Push stack_args (in reverse order so [sp+0] gets the first one)
        try:
            sp = qemu.read_register("sp") & 0xffffffff
            need = len(cfg["stack_args"]) * 4
            if need:
                sp = (sp - need) & 0xffffffff
                qemu.write_register("sp", sp)
                for i, v in enumerate(cfg["stack_args"]):
                    qemu.write_memory(sp + i * 4, 4, v & 0xffffffff)
        except Exception as _e:
            hal_log.error("CallFunction: stack push failed: %s", _e)
        # Set LR and PC
        try:
            qemu.write_register("lr", cfg["return_addr"] & 0xffffffff)
            qemu.write_register("pc", cfg["target"] & 0xffffffff)
        except Exception as _e:
            hal_log.error("CallFunction: pc/lr set failed: %s", _e)
            return True, None
        if not cfg["silent"]:
            hal_log.info("CallFunction: %s -> target=0x%08x  "
                         "args=%s  stack_args=%s  lr=0x%08x",
                         self.func_names[addr], cfg["target"],
                         ["0x%x" % a for a in cfg["args"]],
                         ["0x%x" % a for a in cfg["stack_args"]],
                         cfg["return_addr"])
        return False, None   # observe-only; PC already redirected


class IrqReturnArm(BPHandler):
    """Canonical ARM IRQ-mode exit. Use as an `isr_addr` when no real
    ISR is available -- each delivered IRQ enters this address, the
    handler restores CPSR from SPSR_irq and jumps PC to LR_irq - 4
    (the interrupted instruction).

    Unicorn doesn't fully model ARM exception-return semantics for the
    `subs pc, lr, #4` instruction (CPSR mode isn't restored from
    SPSR_irq). This bp_handler does the restore in Python so subsequent
    IRQ deliveries see CPSR back in SVC mode with I=0.

    With `task_pcs` (list of PC values), the handler instead does a
    round-robin context switch: each tick, pick the next PC in the list
    and return to it (NOT to the interrupted PC). The handler also
    saves and restores a per-task SP so each pseudo-task has its own
    stack. This is the minimal demonstration that the IRQ + scheduler
    chain is functional.

    Halucinator configuration usage:
      # IRQ no-op exit (resume interrupted PC):
      - class: halucinator.bp_handlers.IrqReturnArm
        function: irq_return
        addr: 0x23ff7000

      # Round-robin context switch:
      - class: halucinator.bp_handlers.IrqReturnArm
        function: irq_ctxswitch
        addr: 0x23ff7000
        registration_args:
          task_pcs: [0x23ff7100, 0x23ff7200, 0x23ff7300]
          task_sp_base: 0x23fe0000   # SP for task N = base - N*0x4000
          task_sp_stride: 0x4000
    """

    def __init__(self) -> None:
        self.cfg: Dict[int, dict] = {}
        self.func_names: Dict[int, str] = {}
        self.n: Dict[int, int] = {}
        self.task_idx: Dict[int, int] = {}
        # Per-task saved sp, keyed by (intercept_addr, task_idx).
        self.task_sp: Dict[tuple, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = True,
        task_pcs: Optional[list] = None,
        task_sp_base: int = 0,
        task_sp_stride: int = 0x4000,
        full_context: bool = False,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.cfg[addr] = {
            "task_pcs": list(task_pcs) if task_pcs else None,
            "task_sp_base": task_sp_base,
            "task_sp_stride": task_sp_stride,
            "full_context": full_context,
            "silent": silent,
        }
        self.func_names[addr] = func_name
        self.n[addr] = 0
        self.task_idx[addr] = 0
        # Per-task saved register banks, keyed by (intercept_addr, task_idx).
        if not hasattr(self, "task_regs"):
            self.task_regs: Dict[tuple, dict] = {}
        return cast(HandlerFunction, IrqReturnArm.do_return)

    @bp_handler
    def do_return(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        cfg = self.cfg[addr]
        # In IRQ mode here. Read BANKED LR_irq and SPSR_irq before
        # switching CPSR (which would un-bank them).
        try:
            lr_irq = qemu.read_register("lr") & 0xffffffff
            spsr_irq = qemu.read_register("spsr") & 0xffffffff
            # Restore CPSR (switches to the interrupted mode).
            qemu.write_register("cpsr", spsr_irq)
        except Exception as _e:
            hal_log.error("IrqReturnArm: register juggling failed: %s", _e)
            return True, None
        # In the restored mode now (typically SVC).
        if cfg["task_pcs"]:
            # Context-switch mode: pick the next task in the rotation.
            tasks = cfg["task_pcs"]
            cur_idx = self.task_idx[addr]
            full_ctx = cfg.get("full_context", False)
            # SAVE current task: in full-context mode, snapshot all 13 GP
            # registers + the interrupted PC (lr_irq - 4); in simple mode
            # just SP.
            try:
                cur_sp = qemu.read_register("sp") & 0xffffffff
                self.task_sp[(addr, cur_idx)] = cur_sp
                if full_ctx:
                    bank = {}
                    for r in ("r0","r1","r2","r3","r4","r5","r6","r7",
                              "r8","r9","r10","r11","r12"):
                        try: bank[r] = qemu.read_register(r) & 0xffffffff
                        except Exception: bank[r] = 0
                    # The interrupted PC is lr_irq-4 (banked when IRQ entered)
                    bank["__interrupted_pc"] = (lr_irq - 4) & 0xffffffff
                    self.task_regs[(addr, cur_idx)] = bank
            except Exception:
                pass
            # ROTATE.
            next_idx = (cur_idx + 1) % len(tasks)
            self.task_idx[addr] = next_idx
            next_pc = tasks[next_idx]
            # Restore next task's SP, or allocate it from the pool on first
            # use.
            if (addr, next_idx) in self.task_sp:
                next_sp = self.task_sp[(addr, next_idx)]
            elif cfg["task_sp_base"]:
                next_sp = cfg["task_sp_base"] - next_idx * cfg["task_sp_stride"]
                self.task_sp[(addr, next_idx)] = next_sp
            else:
                next_sp = None
            try:
                if next_sp is not None:
                    qemu.write_register("sp", next_sp)
                if full_ctx and (addr, next_idx) in self.task_regs:
                    # Restore the next task's full register bank, and resume
                    # at the saved interrupted PC (not the entry point).
                    bank = self.task_regs[(addr, next_idx)]
                    for r, v in bank.items():
                        if r == "__interrupted_pc": continue
                        try: qemu.write_register(r, v)
                        except Exception: pass
                    next_pc = bank["__interrupted_pc"]
                # Set LR so execute_return puts PC at next_pc.
                qemu.write_register("lr", next_pc)
            except Exception as _e:
                hal_log.error("IrqReturnArm: ctxsw register failed: %s", _e)
            self.n[addr] += 1
            if self.n[addr] <= 16 or self.n[addr] % 200 == 0:
                hal_log.info("IrqReturnArm: tick #%d -- task switch "
                             "%d -> %d  (PC=0x%08x sp=0x%08x)",
                             self.n[addr], cur_idx, next_idx, next_pc,
                             next_sp if next_sp is not None else 0)
        else:
            # No-context mode: return to interrupted PC (canonical IRQ
            # exit). Set PC DIRECTLY -- do NOT overwrite LR with the
            # return address (that would clobber the caller's saved
            # return address in the restored mode, breaking any
            # function call that's currently in flight).
            try:
                qemu.write_register("pc", (lr_irq - 4) & 0xffffffff)
            except Exception as _e:
                hal_log.error("IrqReturnArm: simple-exit failed: %s", _e)
            self.n[addr] += 1
            if self.n[addr] <= 8 or self.n[addr] % 100 == 0:
                hal_log.info("IrqReturnArm: tick #%d -- restored CPSR=0x%x, "
                             "PC<-0x%08x", self.n[addr], spsr_irq, lr_irq - 4)
            return False, None    # observe-only; PC already set
        return True, None    # task-switch case: execute_return sets PC = LR


class IntLvlVecChkArm(BPHandler):
    """ARM xxxIntLvlVecChk(level_p, vec_p) -- return the pending IRQ.

    VxWorks calls this BSP-defined routine from inside its IRQ handler
    to ask the AIC "which level and which vector caused the interrupt?".
    The function takes two
    pointer args (r0=level_p, r1=vec_p) and writes the answers.

    Our handler returns the level/vec from the latest IRQ injected by
    the TimerModel (queued on backend._pending_irqs). If no IRQ is
    pending, returns level=0, vec=0 (the target PLC's clock IRQ is 0 in our
    config).

    Halucinator configuration:
      - class: halucinator.bp_handlers.IntLvlVecChkArm
        function: mr9200IntLvlVecChk
        addr: 0x20193148
        registration_args: { default_irq: 0, level: 1 }
    """

    def __init__(self) -> None:
        self.cfg: Dict[int, dict] = {}
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        default_irq: int = 0, level: int = 1, silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments, unused-argument
        self.cfg[addr] = {"default_irq": default_irq, "level": level,
                          "silent": silent}
        self.func_names[addr] = func_name
        return cast(HandlerFunction, IntLvlVecChkArm.do_chk)

    @bp_handler
    def do_chk(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        cfg = self.cfg[addr]
        level_p = qemu.read_register("r0") & 0xffffffff
        vec_p = qemu.read_register("r1") & 0xffffffff
        # Pick the queued IRQ from the backend (set by ArmVicController on
        # delivery) if available; else fall back to default_irq.
        try:
            backend_attr = getattr(qemu, "_last_delivered_irq", None)
            irq = backend_attr if backend_attr is not None else cfg["default_irq"]
        except Exception:
            irq = cfg["default_irq"]
        try:
            qemu.write_memory(level_p, 4, cfg["level"])
            qemu.write_memory(vec_p, 4, irq)
        except Exception as _e:
            if not cfg["silent"]:
                hal_log.info("IntLvlVecChkArm: write failed %s", _e)
        if not cfg["silent"]:
            hal_log.info("IntLvlVecChkArm: %s -> level=%d vec=%d "
                         "(level_p=0x%x vec_p=0x%x)",
                         self.func_names[addr], cfg["level"], irq,
                         level_p, vec_p)
        # Return 0 (OK) like the real function would on success;
        # SkipFunc-style return.
        return True, 0


class IntConnectLogger(BPHandler):
    """Log VxWorks intConnect calls and capture (vec, isr, arg) tuples.

    Stores results in a class-level dict so the rest of halucinator
    (e.g. ArmVicController) can query the most-recently-connected ISR
    for a vector. ABI: ARM AAPCS, args in r0/r1/r2.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.IntConnectLogger
        function: intConnect
        addr: 0x201637f0
    """

    # Class-level so multiple intConnect intercepts (or other callers) can
    # share the captured map.
    connections: Dict[int, "tuple[int, int]"] = {}  # vec -> (isr, arg)

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        return cast(HandlerFunction, IntConnectLogger.log_intconnect)

    @bp_handler
    def log_intconnect(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        try:
            vec = qemu.read_register("r0") & 0xffffffff
            isr = qemu.read_register("r1") & 0xffffffff
            arg = qemu.read_register("r2") & 0xffffffff
            lr = qemu.read_register("lr") & 0xffffffff
            IntConnectLogger.connections[vec] = (isr, arg)
            hal_log.info(
                "IntConnect[%s]: vec=0x%08x isr=0x%08x arg=0x%08x  (lr=0x%08x)",
                self.func_names.get(addr, "?"), vec, isr, arg, lr)
        except Exception as _e:
            hal_log.info("IntConnect: failed to read args (%s)", _e)
        return False, None  # let real intConnect run


class FixupTaskSP(BPHandler):
    """Intercept a VxWorks-style context-switch SP load and substitute a
    valid stack address when the loaded value is garbage.

    Boot-time taskSpawn under our partially-stubbed kernel init returns
    bogus task-stack addresses (e.g. 0xfffffff0 from a `0 - 16` underflow
    on an uninitialised memPart). When `reschedule` later switches to that
    task it does `ldreq sp, [r0, #N]` and SP becomes garbage; the first
    push lands in MMIO and pop returns 0 -> PC=0 -> derail.

    This handler fires at the instruction *after* the SP-load. If SP is
    outside the configured `sp_range` (default: usable SDRAM), substitute
    `sp_value` and ALSO write that value back to the TCB at [r0+tcb_ofs]
    so future switches load the same fixed-up value.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.FixupTaskSP
        function: ctxswitch_sp_load
        addr: 0x20165788
        registration_args:
          sp_value: 0x23ff0000          # replacement task SP
          sp_min:   0x20000000          # below this -> fix
          sp_max:   0x24000000          # at-or-above this -> fix
          tcb_ofs:  0x60                # offset in TCB (relative to r0)
    """

    def __init__(self, sp_value: int = 0x23ff0000,
                 sp_min: int = 0x20000000, sp_max: int = 0x24000000,
                 tcb_ofs: int = 0x60, pool_top: int = 0,
                 stack_size: int = 0x2000) -> None:
        self.sp_value = sp_value
        self.sp_min = sp_min
        self.sp_max = sp_max
        self.tcb_ofs = tcb_ofs
        # pool_top != 0 enables per-task stacks: each FixupTaskSP fire
        # allocates a fresh stride-sized stack starting `pool_top` and
        # decrementing by `stack_size` each call. Different tasks then
        # get different (and disjoint) stacks.
        self.pool_top = pool_top
        self.stack_size = stack_size
        self.next_stack = pool_top
        # Remember TCB -> task-stack so the same task gets the same stack
        # on subsequent context switches.
        self.tcb_to_sp: Dict[int, int] = {}
        self.func_names: Dict[int, str] = {}
        self.silent: Dict[int, bool] = {}
        self.n: Dict[int, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        silent: bool = False,
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        self.silent[addr] = silent
        self.n[addr] = 0
        return cast(HandlerFunction, FixupTaskSP.fixup)

    @bp_handler
    def fixup(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        sp = qemu.read_register("sp") & 0xffffffff
        if self.sp_min <= sp < self.sp_max:
            return False, None  # already in good range — let exec continue
        # Pool mode: per-task disjoint stacks
        r0 = 0
        try:
            r0 = qemu.read_register("r0") & 0xffffffff
        except Exception:
            pass
        if self.pool_top and self.sp_min <= r0 < self.sp_max:
            # If we've seen this TCB before, reuse its stack
            if r0 in self.tcb_to_sp:
                new_sp = self.tcb_to_sp[r0]
            else:
                new_sp = self.next_stack
                self.tcb_to_sp[r0] = new_sp
                self.next_stack -= self.stack_size
        else:
            new_sp = self.sp_value
        # If the per-task TCB pointer is in r0, patch TCB+tcb_ofs too so
        # subsequent context switches reload the same good SP.
        try:
            if self.sp_min <= r0 < self.sp_max:
                qemu.write_memory(r0 + self.tcb_ofs, 4, new_sp)
        except Exception:
            pass
        qemu.write_register("sp", new_sp)
        self.n[addr] += 1
        if not self.silent[addr] and self.n[addr] <= 16:
            hal_log.info("FixupTaskSP: %s @ 0x%08x  bad sp=0x%08x -> 0x%08x"
                         "  (tcb=0x%08x)",
                         self.func_names[addr], addr, sp, new_sp, r0)
        return False, None  # let the instruction at addr execute


class Memset(BPHandler):
    """Faithful memset(dest, byte, count) implementation in Python.

    Useful when the firmware's own memset is too slow to emulate (bulk
    `stmia r0!,{...}` loops over megabytes of .bss take many minutes
    under unicorn). Reads ABI args dest/byte/count, writes the byte
    `count` times to memory at `dest`, sets the return value to `dest`
    (per POSIX), and returns control to the caller.

    Halucinator configuration usage:
      - class: halucinator.bp_handlers.Memset
        function: memset
        addr: <addr of memset entry>
    """

    def __init__(self) -> None:
        self.func_names: Dict[int, str] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str, silent: bool = False
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        self.func_names[addr] = func_name
        return cast(HandlerFunction, Memset.do_memset)

    @bp_handler
    def do_memset(self, qemu: "HalBackend", addr: int) -> HandlerReturn:  # pylint: disable=unused-argument
        dest = qemu.get_arg(0) & 0xffffffff
        byte = qemu.get_arg(1) & 0xff
        count = qemu.get_arg(2) & 0xffffffff
        if count > 0x4000000:  # 64 MB cap, prevents runaway from bad args
            hal_log.warning("Memset: huge count=0x%x at dest=0x%x; clamping",
                            count, dest)
            count = 0x4000000
        if count > 0:
            payload = bytes([byte]) * count
            qemu.write_memory_bytes(dest, payload)
        hal_log.info("Memset: dest=0x%08x byte=0x%02x count=0x%x", dest, byte, count)
        return True, dest  # POSIX: returns dest


class SleepTime(BPHandler):
    """
    Intercepts a sleep/delay function and optionally pauses Python-side
    execution instead of letting the firmware spin.
    """

    def __init__(self) -> None:
        self.sleep_times: Dict[int, int] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str, sleep_time: int = 0
    ) -> HandlerFunction:
        self.sleep_times[addr] = sleep_time
        return self.sleep_time

    @bp_handler(["sleep", "usleep", "msleep", "vTaskDelay"])
    def sleep_time(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        duration = self.sleep_times.get(addr, 0)
        time.sleep(duration)
        return False, 0
