# Copyright 2026 Christopher Wright

from __future__ import annotations

from typing import Any

from halucinator.qemu_targets.hal_qemu import HALQemuTarget


class X86QemuTarget(HALQemuTarget):
    """
        QEMU target for 32-bit x86 / i386 with halucinator function-arg
        helpers. Uses the System V i386 cdecl calling convention:

          * Arguments are passed on the stack (no register args). Arg 0 is
            at [esp + 4] (the word at [esp] is the return address).
          * The return value is in EAX.
          * The return address is the word at the top of the stack ([esp]),
            and `ret` pops it.

        This is the convention VxWorks (and gcc -m32) use for the i386
        VxWorks RTU image.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(X86QemuTarget, self).__init__(*args, **kwargs)

    def get_arg(self, idx: int) -> int:
        if idx < 0:
            raise ValueError("Invalid arg index")
        # cdecl: [esp] = return addr, [esp+4] = arg0, [esp+8] = arg1, ...
        sp = self.read_register("esp")
        return self.read_memory_word(sp + (idx + 1) * 4)

    def set_arg(self, idx: int, value: int) -> None:
        if idx < 0:
            raise ValueError(idx)
        sp = self.read_register("esp")
        self.write_memory_word(sp + (idx + 1) * 4, value)

    def get_ret_addr(self) -> int:
        # Return address is the word at the top of the stack.
        sp = self.read_register("esp")
        return self.read_memory_word(sp)

    def set_ret_addr(self, ret_addr: int) -> None:
        sp = self.read_register("esp")
        self.write_memory_word(sp, ret_addr)

    def execute_return(self, ret_value: int) -> None:
        if ret_value is not None:
            self.regs.eax = ret_value & 0xFFFFFFFF
        # Pop the return address off the stack and jump to it (emulate `ret`).
        sp = self.read_register("esp")
        ret_addr = self.read_memory_word(sp)
        self.write_register("esp", sp + 4)
        self.regs.pc = ret_addr
