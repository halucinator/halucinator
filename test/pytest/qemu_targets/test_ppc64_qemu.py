"""
Unit tests for PowerPC64QemuTarget using mocks (no QEMU binary required).

Note: PowerPC64QemuTarget inherits from HALQemuTarget. It uses 64-bit
stack offsets (8 bytes) and does not truncate return values.
"""
from unittest.mock import MagicMock, patch

import pytest


def _make_ppc64_target():
    """
    Build a PowerPC64QemuTarget with all heavy avatar2 machinery mocked out.
    """
    with patch("halucinator.qemu_targets.hal_qemu.QemuTarget.__init__", return_value=None), \
         patch("halucinator.qemu_targets.hal_qemu.HALQemuTarget._init_halucinator_heap"):
        from halucinator.qemu_targets.powerpc64_qemu import PowerPC64QemuTarget
        target = PowerPC64QemuTarget.__new__(PowerPC64QemuTarget)
        # Minimal init
        target.irq_base_addr = None
        target.calls_memory_blocks = {}
        target.REGISTER_IRQ_OFFSET = 4

        # Mock register and memory access
        target.read_register = MagicMock()
        target.write_register = MagicMock()
        target.read_memory = MagicMock()
        target.write_memory = MagicMock()
        target.read_memory_word = MagicMock()
        target.write_memory_word = MagicMock()

        # Mock regs
        target.regs = MagicMock()

        # Mock avatar config
        target.avatar = MagicMock()
        target.avatar.config.memories = {}

        return target


class TestPowerPC64QemuTargetImport:
    def test_import(self):
        from halucinator.qemu_targets.powerpc64_qemu import PowerPC64QemuTarget
        assert PowerPC64QemuTarget is not None


class TestPowerPC64QemuTargetGetArg:
    def test_get_arg_register_0(self):
        """Arg 0 maps to r3"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0xAAAA
        result = target.get_arg(0)
        target.read_register.assert_called_once_with("r3")
        assert result == 0xAAAA

    def test_get_arg_register_7(self):
        """Arg 7 maps to r10"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0xBBBB
        result = target.get_arg(7)
        target.read_register.assert_called_once_with("r10")
        assert result == 0xBBBB

    def test_get_arg_stack_8(self):
        """Arg 8 is the first stack argument, at sp + 0 with 8-byte stride"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0x4000  # sp
        target.read_memory_word.return_value = 0xCCCC
        result = target.get_arg(8)
        target.read_register.assert_called_once_with("sp")
        target.read_memory_word.assert_called_once_with(0x4000)
        assert result == 0xCCCC

    def test_get_arg_stack_9(self):
        """Arg 9 is at sp + 8 (8-byte stride)"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0x4000  # sp
        target.read_memory_word.return_value = 0xDDDD
        result = target.get_arg(9)
        target.read_memory_word.assert_called_once_with(0x4008)
        assert result == 0xDDDD

    def test_get_arg_negative_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(ValueError, match="Invalid arg index"):
            target.get_arg(-1)


class TestPowerPC64QemuTargetSetArg:
    def test_set_arg_register_0(self):
        """Arg 0 maps to r3"""
        target = _make_ppc64_target()
        target.set_arg(0, 42)
        target.write_register.assert_called_once_with("r3", 42)

    def test_set_arg_register_7(self):
        """Arg 7 maps to r10"""
        target = _make_ppc64_target()
        target.set_arg(7, 99)
        target.write_register.assert_called_once_with("r10", 99)

    def test_set_arg_stack_8(self):
        """Arg 8 goes to stack at sp + 0 with 8-byte stride"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0x5000  # sp
        target.set_arg(8, 0xABCD)
        target.read_register.assert_called_once_with("sp")
        target.write_memory_word.assert_called_once_with(0x5000, 0xABCD)

    def test_set_arg_stack_9(self):
        """Arg 9 goes to stack at sp + 8"""
        target = _make_ppc64_target()
        target.read_register.return_value = 0x5000
        target.set_arg(9, 0x1234)
        target.write_memory_word.assert_called_once_with(0x5008, 0x1234)

    def test_set_arg_negative_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(ValueError):
            target.set_arg(-1, 0)


class TestPowerPC64QemuTargetExecuteReturn:
    def test_execute_return_sets_r3_and_pc(self):
        target = _make_ppc64_target()
        target.regs.lr = 0x8000
        target.execute_return(0x12345678)
        assert target.regs.r3 == 0x12345678
        assert target.regs.pc == 0x8000

    def test_execute_return_does_not_truncate_64_bit(self):
        """PPC64 does NOT truncate to 32 bits (unlike PPC32)"""
        target = _make_ppc64_target()
        target.regs.lr = 0x4000
        target.execute_return(0x1FFFFFFFF)
        assert target.regs.r3 == 0x1FFFFFFFF
        assert target.regs.pc == 0x4000

    def test_execute_return_none_does_not_set_r3(self):
        target = _make_ppc64_target()
        target.regs.lr = 0x6000
        target.execute_return(None)
        assert target.regs.pc == 0x6000

    def test_execute_return_zero(self):
        target = _make_ppc64_target()
        target.regs.lr = 0x7000
        target.execute_return(0)
        assert target.regs.r3 == 0
        assert target.regs.pc == 0x7000


class TestPowerPC64QemuTargetRetAddr:
    def test_get_ret_addr(self):
        target = _make_ppc64_target()
        target.regs.lr = 0xABCD
        assert target.get_ret_addr() == 0xABCD

    def test_set_ret_addr(self):
        target = _make_ppc64_target()
        target.set_ret_addr(0x9999)
        assert target.regs.lr == 0x9999


class TestPowerPC64QemuTargetIRQ:
    def test_irq_set_qmp_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.irq_set_qmp()

    def test_irq_clear_qmp_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.irq_clear_qmp()

    def test_irq_set_bp_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.irq_set_bp()

    def test_irq_clear_bp_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.irq_clear_bp()

    def test_irq_pulse_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.irq_pulse()

    def test_get_irq_base_addr_raises(self):
        target = _make_ppc64_target()
        with pytest.raises(NotImplementedError):
            target.get_irq_base_addr()
