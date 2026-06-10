from __future__ import annotations

from typing import Any

from halucinator.qemu_targets.hal_qemu import HALQemuTarget


class MIPSQemuTarget(HALQemuTarget):
    """
        Implements a QEMU target that has function args for use with
        halucinator.  Enables read/writing and returning from
        functions in a calling convention aware manner
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super(MIPSQemuTarget, self).__init__(*args, **kwargs)

    def get_arg(self, idx: int) -> int:
        """
            Gets the value for a function argument (zero indexed)
            :param idx  The argument index to return
            :returns    Argument value
        """
        if idx >= 0 and idx < 4:  # first 4 in regs, then on stack
            return self.read_register("a%i" % idx)
        elif idx >= 4:
            sp = self.read_register("sp")
            stack_addr = sp + (idx - 4) * 4
            return self.read_memory_word(stack_addr)
        else:
            raise ValueError("Invalid arg index")

    def set_arg(self, idx: int, value: int) -> None:
        """
            Sets the value for a function argument (zero indexed)

            :param idx      The argument index to return
            :param value    Value to set index to
        """
        if idx >= 0 and idx < 4:
            self.write_register("a%i" % idx, value)
        elif idx >= 4:
            sp = self.read_register("sp")
            stack_addr = sp + (idx - 4) * 4
            self.write_memory_word(stack_addr, value)
        else:
            raise ValueError(idx)

    def get_ret_addr(self) -> int:
        """
            Gets the return address for the function call

            :returns Return address of the function call
        """
        return self.regs.ra

    def set_ret_addr(self, ret_addr: int) -> None:
        """
            Sets the return address for the function call
            :param ret_addr Value for return address
        """
        self.regs.ra = ret_addr

    def execute_return(self, ret_value: int) -> None:
        if ret_value != None:
            # Puts ret value in v0
            # TODO: if longer than 1 word, need to split ret_value and put in v0 and v1
            self.regs.v0 = ret_value & 0xFFFFFFFF  # Truncate to 32 bits
        self.regs.pc = self.regs.ra

    def inject_irq(self, irq_num: int) -> None:
        """Deliver IRQ *irq_num* to the firmware.

        Prefers avatar-qemu's avatar-shadow-irq QMP command when
        the YAML interrupt_controller declares physical
        irq_*_phys_addr fields — that writes the post-ack state
        straight into the firmware's RAM globals on the iothread
        (under BQL, no GDB stub involvement) and bypasses CPU
        exception machinery entirely. The MIPS firmware just polls
        irq_fired and emits the magic UART output once it flips.

        Falls back to avatar-mips-inject-irq (Cause.IP[N] pulse)
        for configs that don't have shadow-state addresses — those
        rely on the firmware's CP0 exception entry, which only the
        few MIPS variants we care about model accurately.
        """
        ctrl = getattr(self, "_irq_controller", None)
        irq_fired_phys = getattr(ctrl, "irq_fired_phys_addr", None)
        irq_number_phys = getattr(ctrl, "irq_number_phys_addr", None)
        if irq_fired_phys is not None and irq_number_phys is not None:
            self.protocols.monitor.execute_command(
                "avatar-shadow-irq",
                args={"number-addr": int(irq_number_phys),
                      "fired-addr":  int(irq_fired_phys),
                      "irq-num":     int(irq_num)},
            )
            return
        self.protocols.monitor.execute_command(
            "avatar-mips-inject-irq",
            args={"num-irq": int(irq_num), "num-cpu": 0},
        )
