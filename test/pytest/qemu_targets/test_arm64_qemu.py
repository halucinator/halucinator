"""
Unit tests for ARM64QemuTarget using mocks (no QEMU binary required).

ARM64QemuTarget inherits from ARMQemuTarget. Key differences:
- get_arg uses x0-x7 registers (8 register args, 8-byte stack stride)
- execute_return sets x0 (no 32-bit truncation) and pc = x30
- set_arg is inherited from ARMQemuTarget (uses r0-r3 for first 4 args)
- IRQ methods come from ARMQemuTarget and raise TypeError without controller
"""
from unittest.mock import MagicMock, patch

import pytest


def _make_arm64_target():
    """
    Build an ARM64QemuTarget with all heavy avatar2 machinery mocked out.
    """
    with patch("avatar2.QemuTarget.__init__", return_value=None), \
         patch.object(
             _get_arm_class(), "_init_halucinator_heap", return_value=None
         ):
        from halucinator.qemu_targets.arm64_qemu import ARM64QemuTarget
        target = ARM64QemuTarget.__new__(ARM64QemuTarget)
        # Minimal init
        target.irq_base_addr = None
        target.calls_memory_blocks = {}
        target.REGISTER_IRQ_OFFSET = 4

        # Mock register and memory access
        target.read_register = MagicMock()
        target.write_register = MagicMock()
        target.read_memory = MagicMock()
        target.write_memory = MagicMock()

        # Mock regs
        target.regs = MagicMock()

        # Mock avatar config for IRQ tests
        target.avatar = MagicMock()
        target.avatar.config.memories = {}

        return target


def _get_arm_class():
    from halucinator.qemu_targets.arm_qemu import ARMQemuTarget
    return ARMQemuTarget


class TestARM64QemuTargetImport:
    def test_import(self):
        from halucinator.qemu_targets.arm64_qemu import ARM64QemuTarget
        assert ARM64QemuTarget is not None

    def test_inherits_from_arm(self):
        from halucinator.qemu_targets.arm_qemu import ARMQemuTarget
        from halucinator.qemu_targets.arm64_qemu import ARM64QemuTarget
        assert issubclass(ARM64QemuTarget, ARMQemuTarget)


class TestARM64QemuTargetGetArg:
    def test_get_arg_register_0(self):
        """Arg 0 maps to x0"""
        target = _make_arm64_target()
        target.read_register.return_value = 0xDEAD
        result = target.get_arg(0)
        target.read_register.assert_called_once_with("x0")
        assert result == 0xDEAD

    def test_get_arg_register_7(self):
        """Arg 7 maps to x7"""
        target = _make_arm64_target()
        target.read_register.return_value = 0xBEEF
        result = target.get_arg(7)
        target.read_register.assert_called_once_with("x7")
        assert result == 0xBEEF

    def test_get_arg_stack_8(self):
        """Arg 8 is the first stack argument, at sp + 0 with 8-byte stride"""
        target = _make_arm64_target()
        target.read_register.return_value = 0x10000  # sp
        target.read_memory.return_value = 0xCAFE
        result = target.get_arg(8)
        target.read_register.assert_called_once_with("sp")
        target.read_memory.assert_called_once_with(0x10000, 8, 1)
        assert result == 0xCAFE

    def test_get_arg_stack_9(self):
        """Arg 9 is at sp + 8 (8-byte stride)"""
        target = _make_arm64_target()
        target.read_register.return_value = 0x10000  # sp
        target.read_memory.return_value = 0xF00D
        result = target.get_arg(9)
        target.read_memory.assert_called_once_with(0x10008, 8, 1)
        assert result == 0xF00D

    def test_get_arg_negative_raises(self):
        target = _make_arm64_target()
        with pytest.raises(ValueError, match="Invalid arg index"):
            target.get_arg(-1)


class TestARM64QemuTargetSetArg:
    """ARM64 inherits set_arg from ARMQemuTarget (r0-r3 for register args)."""

    def test_set_arg_register_0(self):
        target = _make_arm64_target()
        target.set_arg(0, 42)
        target.write_register.assert_called_once_with("r0", 42)

    def test_set_arg_register_3(self):
        target = _make_arm64_target()
        target.set_arg(3, 99)
        target.write_register.assert_called_once_with("r3", 99)

    def test_set_arg_stack_4(self):
        target = _make_arm64_target()
        target.read_register.return_value = 0x20000  # sp
        target.set_arg(4, 0xABCD)
        target.read_register.assert_called_once_with("sp")
        target.write_memory.assert_called_once_with(0x20000, 4, 0xABCD)

    def test_set_arg_negative_raises(self):
        target = _make_arm64_target()
        with pytest.raises(ValueError):
            target.set_arg(-1, 0)


class TestARM64QemuTargetExecuteReturn:
    def test_execute_return_sets_x0_and_pc_from_x30(self):
        target = _make_arm64_target()
        target.regs.x30 = 0x8000
        target.execute_return(0x12345678)
        assert target.regs.x0 == 0x12345678
        assert target.regs.pc == 0x8000

    def test_execute_return_does_not_truncate(self):
        """ARM64 does NOT truncate return values to 32 bits"""
        target = _make_arm64_target()
        target.regs.x30 = 0x4000
        target.execute_return(0x1FFFFFFFF)
        assert target.regs.x0 == 0x1FFFFFFFF
        assert target.regs.pc == 0x4000

    def test_execute_return_none_does_not_set_x0(self):
        target = _make_arm64_target()
        target.regs.x30 = 0x6000
        target.execute_return(None)
        assert target.regs.pc == 0x6000

    def test_execute_return_zero(self):
        target = _make_arm64_target()
        target.regs.x30 = 0x7000
        target.execute_return(0)
        assert target.regs.x0 == 0
        assert target.regs.pc == 0x7000


class TestARM64QemuTargetRetAddr:
    """ARM64 inherits get_ret_addr / set_ret_addr from ARMQemuTarget (uses lr)."""

    def test_get_ret_addr(self):
        target = _make_arm64_target()
        target.regs.lr = 0xABCD
        assert target.get_ret_addr() == 0xABCD

    def test_set_ret_addr(self):
        target = _make_arm64_target()
        target.set_ret_addr(0x9999)
        assert target.regs.lr == 0x9999


class TestARM64QemuTargetIRQ:
    """ARM64 inherits IRQ methods from ARMQemuTarget which raise TypeError
    when no halucinator-irq memory is configured."""

    def test_irq_set_qmp_raises_without_irq_controller(self):
        target = _make_arm64_target()
        # _get_irq_path calls _get_qom_list -> protocols.monitor.execute_command
        # which returns a list of QOM items. An empty list means no IRQ controller.
        target.protocols = MagicMock()
        target.protocols.monitor.execute_command.return_value = []
        with pytest.raises(TypeError, match="No Interrupt Controller found"):
            target.irq_set_qmp()

    def test_irq_clear_qmp_raises_without_irq_controller(self):
        target = _make_arm64_target()
        target.protocols = MagicMock()
        target.protocols.monitor.execute_command.return_value = []
        with pytest.raises(TypeError, match="No Interrupt Controller found"):
            target.irq_clear_qmp()

    def test_irq_enable_qmp_raises_without_irq_controller(self):
        target = _make_arm64_target()
        target.protocols = MagicMock()
        target.protocols.monitor.execute_command.return_value = []
        with pytest.raises(TypeError, match="No Interrupt Controller found"):
            target.irq_enable_qmp()
