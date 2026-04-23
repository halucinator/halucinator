"""
Unit tests for PowerPCQemuTarget using mocks (no QEMU binary required).

Note: PowerPCQemuTarget inherits directly from avatar2.QemuTarget (not
HALQemuTarget), and its IRQ methods all raise NotImplementedError.
"""
from unittest.mock import MagicMock, patch

import pytest


def _make_ppc_target():
    """
    Build a PowerPCQemuTarget with all heavy avatar2 machinery mocked out.
    """
    with patch("avatar2.QemuTarget.__init__", return_value=None):
        from halucinator.qemu_targets.powerpc_qemu import PowerPCQemuTarget
        target = PowerPCQemuTarget.__new__(PowerPCQemuTarget)
        # Manually set attributes that __init__ would create
        target.irq_base_addr = None
        target.calls_memory_blocks = {}

        # Mock register and memory access
        target.read_register = MagicMock()
        target.write_register = MagicMock()
        target.read_memory = MagicMock()
        target.write_memory = MagicMock()

        # Mock regs as a simple namespace
        target.regs = MagicMock()

        # Mock avatar for plugin loading and config
        target.avatar = MagicMock()
        target.avatar.config.memories = {}

        return target


class TestPowerPCQemuTargetImport:
    def test_import(self):
        from halucinator.qemu_targets.powerpc_qemu import PowerPCQemuTarget
        assert PowerPCQemuTarget is not None


class TestPowerPCQemuTargetGetArg:
    def test_get_arg_register_0(self):
        """Arg 0 maps to r3"""
        target = _make_ppc_target()
        target.read_register.return_value = 0xAAAA
        result = target.get_arg(0)
        target.read_register.assert_called_once_with("r3")
        assert result == 0xAAAA

    def test_get_arg_register_7(self):
        """Arg 7 maps to r10"""
        target = _make_ppc_target()
        target.read_register.return_value = 0xBBBB
        result = target.get_arg(7)
        target.read_register.assert_called_once_with("r10")
        assert result == 0xBBBB

    def test_get_arg_stack_8(self):
        """Arg 8 is the first stack argument"""
        target = _make_ppc_target()
        target.read_register.return_value = 0x3000  # sp
        target.read_memory.return_value = 0xCCCC
        result = target.get_arg(8)
        target.read_register.assert_called_once_with("sp")
        target.read_memory.assert_called_once_with(0x3000, 4, 1)
        assert result == 0xCCCC

    def test_get_arg_stack_9(self):
        """Arg 9 is at sp + 4"""
        target = _make_ppc_target()
        target.read_register.return_value = 0x3000  # sp
        target.read_memory.return_value = 0xDDDD
        result = target.get_arg(9)
        target.read_memory.assert_called_once_with(0x3004, 4, 1)
        assert result == 0xDDDD

    def test_get_arg_negative_raises(self):
        target = _make_ppc_target()
        with pytest.raises(ValueError, match="Invalid arg index"):
            target.get_arg(-1)


class TestPowerPCQemuTargetSetArg:
    def test_set_arg_register_0(self):
        """Arg 0 maps to r3"""
        target = _make_ppc_target()
        target.set_arg(0, 42)
        target.write_register.assert_called_once_with("r3", 42)

    def test_set_arg_register_7(self):
        """Arg 7 maps to r10"""
        target = _make_ppc_target()
        target.set_arg(7, 99)
        target.write_register.assert_called_once_with("r10", 99)

    def test_set_arg_stack_raises(self):
        """Stack args (idx >= 8) raise NotImplementedError"""
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.set_arg(8, 0)


class TestPowerPCQemuTargetExecuteReturn:
    def test_execute_return_sets_r3_and_pc(self):
        target = _make_ppc_target()
        target.regs.lr = 0x8000
        target.execute_return(0x12345678)
        assert target.regs.r3 == 0x12345678
        assert target.regs.pc == 0x8000

    def test_execute_return_truncates_to_32_bits(self):
        target = _make_ppc_target()
        target.regs.lr = 0x4000
        target.execute_return(0x1FFFFFFFF)
        assert target.regs.r3 == 0xFFFFFFFF
        assert target.regs.pc == 0x4000

    def test_execute_return_none_does_not_set_r3(self):
        target = _make_ppc_target()
        target.regs.lr = 0x6000
        target.execute_return(None)
        assert target.regs.pc == 0x6000

    def test_execute_return_zero(self):
        target = _make_ppc_target()
        target.regs.lr = 0x7000
        target.execute_return(0)
        assert target.regs.r3 == 0
        assert target.regs.pc == 0x7000


class TestPowerPCQemuTargetRetAddr:
    def test_get_ret_addr(self):
        target = _make_ppc_target()
        target.regs.lr = 0xABCD
        assert target.get_ret_addr() == 0xABCD

    def test_set_ret_addr(self):
        target = _make_ppc_target()
        target.set_ret_addr(0x9999)
        assert target.regs.lr == 0x9999


class TestPowerPCQemuTargetIRQ:
    def test_irq_set_qmp_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.irq_set_qmp()

    def test_irq_clear_qmp_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.irq_clear_qmp()

    def test_irq_set_bp_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.irq_set_bp()

    def test_irq_clear_bp_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.irq_clear_bp(1)

    def test_irq_pulse_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.irq_pulse()

    def test_get_irq_base_addr_raises(self):
        target = _make_ppc_target()
        with pytest.raises(NotImplementedError):
            target.get_irq_base_addr()
