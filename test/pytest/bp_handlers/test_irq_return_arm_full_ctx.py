"""Tests for halucinator.bp_handlers.IrqReturnArm full_context mode.

Phase-23 added full_context save/restore to IrqReturnArm's round-robin
mode -- on each IRQ-driven task switch, all 13 GP registers + the
interrupted PC are saved per task and restored on rotation. This is
the architectural definition of a kernel scheduler.

These tests verify the save/restore mechanism with a mock backend.
"""

from unittest.mock import MagicMock

from halucinator.bp_handlers.generic.common import IrqReturnArm


class FakeArmBackend:
    """Mock backend modeling ARM registers + memory for IrqReturnArm tests.

    Tracks register writes so we can assert each task's bank gets
    saved/restored independently across context switches.
    """

    def __init__(self):
        # Register file: r0..r15, sp, lr, pc, cpsr, spsr (banked)
        self.regs = {
            "r0": 0, "r1": 0, "r2": 0, "r3": 0,
            "r4": 0, "r5": 0, "r6": 0, "r7": 0,
            "r8": 0, "r9": 0, "r10": 0, "r11": 0, "r12": 0,
            "sp": 0, "lr": 0, "pc": 0,
            "cpsr": 0x60000012,    # IRQ mode (sample CPSR seen on entry)
            "spsr": 0x60000013,    # saved SVC mode
        }
        self.writes = []

    def read_register(self, name):
        return self.regs.get(name, 0)

    def write_register(self, name, value):
        self.regs[name] = value & 0xffffffff
        self.writes.append((name, value & 0xffffffff))

    def write_memory(self, *a, **kw):
        pass


class TestIrqReturnArmFullContext:
    """full_context: per-task register-bank save/restore on IRQ rotation."""

    def _build_handler(self, *, full_context: bool = True):
        h = IrqReturnArm()
        h.register_handler(
            qemu=None, addr=0x23ff7000, func_name="irq_test",
            task_pcs=[0x23ff7100, 0x23ff7200, 0x23ff7400],
            task_sp_base=0x23ff0000,
            task_sp_stride=0x4000,
            full_context=full_context,
        )
        return h

    def test_full_context_registration_creates_task_regs_attr(self):
        h = self._build_handler()
        assert hasattr(h, "task_regs")
        assert isinstance(h.task_regs, dict)
        # No tasks have run yet
        assert len(h.task_regs) == 0

    def test_first_tick_rotates_to_task_1_no_bank_restore(self):
        """First IRQ from task 0: rotate to task 1, no saved bank yet, so
        PC = task_pcs[1] (entry, not interrupted)."""
        h = self._build_handler()
        be = FakeArmBackend()
        be.regs["lr"] = 0x12345678 + 4    # banked LR_irq = interrupted_pc + 4
        be.regs["spsr"] = 0x60000013
        be.regs["sp"] = 0x23ff_fff0       # task 0's working SP

        result = h.do_return(be, 0x23ff7000)

        # Handler should have rotated task_idx to 1
        assert h.task_idx[0x23ff7000] == 1
        # Saved task 0's bank (full context)
        assert (0x23ff7000, 0) in h.task_regs
        saved = h.task_regs[(0x23ff7000, 0)]
        # The interrupted PC stored is lr_irq - 4
        assert saved["__interrupted_pc"] == 0x12345678
        # Task 1's SP allocated from pool: base - 1*stride
        assert be.regs["sp"] == 0x23ff0000 - 1 * 0x4000
        # LR set so execute_return puts PC at task 1's entry
        assert be.regs["lr"] == 0x23ff7200
        # execute_return mode: do_return returns (True, None)
        assert result == (True, None)

    def test_second_tick_saves_task_1_full_context_then_rotates_to_task_2(self):
        """After task 1 runs and is interrupted, all its GP registers
        should land in the saved bank."""
        h = self._build_handler()
        be = FakeArmBackend()

        # Simulate first IRQ: task 0 -> task 1
        be.regs["lr"] = 0x10000 + 4
        be.regs["spsr"] = 0x60000013
        be.regs["sp"] = 0x23ff_fff0
        h.do_return(be, 0x23ff7000)

        # Now simulate task 1 having run for a bit -- set custom register
        # values that should land in task 1's saved bank on next IRQ.
        for i in range(13):
            be.regs[f"r{i}"] = 0xCAFE0000 | i
        be.regs["sp"] = 0x23ff0000 - 0x4000 - 0x20  # task 1 used some stack
        be.regs["lr"] = 0x20000 + 4   # interrupted PC = 0x20000
        be.regs["spsr"] = 0x60000013

        # Second IRQ: task 1 -> task 2
        h.do_return(be, 0x23ff7000)

        assert h.task_idx[0x23ff7000] == 2
        # Task 1's saved bank should contain all its custom register values
        bank1 = h.task_regs[(0x23ff7000, 1)]
        for i in range(13):
            assert bank1[f"r{i}"] == (0xCAFE0000 | i)
        assert bank1["__interrupted_pc"] == 0x20000

    def test_rotation_back_to_task_1_restores_its_bank(self):
        """After 0->1, 1->2, 2->0, 0->1 cycle, task 1's saved bank should
        be restored when we rotate back to it."""
        h = self._build_handler()
        be = FakeArmBackend()
        be.regs["spsr"] = 0x60000013

        # 0 -> 1 (first time task 1, no bank, PC = task_pcs[1])
        be.regs["lr"] = 0x1000 + 4
        h.do_return(be, 0x23ff7000)

        # Task 1 runs, populates its registers
        for i in range(13):
            be.regs[f"r{i}"] = 0xBEEF0000 | i
        be.regs["lr"] = 0xABCD0000 + 4   # interrupted PC marker
        h.do_return(be, 0x23ff7000)    # 1 -> 2

        # Task 2 runs briefly, different register values
        for i in range(13):
            be.regs[f"r{i}"] = 0xDEAD0000 | i
        be.regs["lr"] = 0x33330000 + 4
        h.do_return(be, 0x23ff7000)    # 2 -> 0

        # Task 0 runs, different again
        for i in range(13):
            be.regs[f"r{i}"] = 0xFACE0000 | i
        be.regs["lr"] = 0x44440000 + 4
        h.do_return(be, 0x23ff7000)    # 0 -> 1 (restore task 1's bank!)

        # Task 1's bank must have been restored
        for i in range(13):
            assert be.regs[f"r{i}"] == (0xBEEF0000 | i), (
                f"r{i} expected 0xBEEF000{i:x}, got 0x{be.regs[f'r{i}']:08x}"
            )
        # PC (via LR) restored to task 1's saved interrupted PC
        assert be.regs["lr"] == 0xABCD0000

    def test_simple_mode_does_not_save_regs(self):
        """full_context=False: no task_regs entries should accumulate."""
        h = IrqReturnArm()
        h.register_handler(
            qemu=None, addr=0x23ff7000, func_name="irq_simple_mode",
            task_pcs=[0x23ff7100, 0x23ff7200],
            task_sp_base=0x23ff0000,
            task_sp_stride=0x4000,
            full_context=False,
        )
        be = FakeArmBackend()
        be.regs["lr"] = 0x99999999
        be.regs["spsr"] = 0x60000013

        h.do_return(be, 0x23ff7000)
        h.do_return(be, 0x23ff7000)

        # Simple mode: only SP gets saved, no GP register banks
        assert len(h.task_regs) == 0
        # But task_sp does track SP
        assert (0x23ff7000, 0) in h.task_sp
