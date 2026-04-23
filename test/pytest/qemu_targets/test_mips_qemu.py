"""
Unit tests for MIPSQemuTarget using mocks (no QEMU binary required).
"""
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


def _make_mips_target():
    """
    Build a MIPSQemuTarget with all heavy avatar2 machinery mocked out.
    """
    with patch("halucinator.qemu_targets.hal_qemu.QemuTarget.__init__", return_value=None), \
         patch("halucinator.qemu_targets.hal_qemu.HALQemuTarget._init_halucinator_heap"):
        from halucinator.qemu_targets.mips_qemu import MIPSQemuTarget
        target = MIPSQemuTarget.__new__(MIPSQemuTarget)
        # Minimal init without calling real __init__
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

        # Mock regs as a simple namespace
        target.regs = MagicMock()

        # Mock avatar config for IRQ tests
        target.avatar = MagicMock()
        target.avatar.config.memories = {}

        return target


class TestMIPSQemuTargetImport:
    def test_import(self):
        from halucinator.qemu_targets.mips_qemu import MIPSQemuTarget
        assert MIPSQemuTarget is not None


class TestMIPSQemuTargetGetArg:
    def test_get_arg_register_0(self):
        target = _make_mips_target()
        target.read_register.return_value = 0xDEAD
        result = target.get_arg(0)
        target.read_register.assert_called_once_with("a0")
        assert result == 0xDEAD

    def test_get_arg_register_3(self):
        target = _make_mips_target()
        target.read_register.return_value = 0xBEEF
        result = target.get_arg(3)
        target.read_register.assert_called_once_with("a3")
        assert result == 0xBEEF

    def test_get_arg_stack_4(self):
        target = _make_mips_target()
        target.read_register.return_value = 0x1000  # sp
        target.read_memory_word.return_value = 0xCAFE
        result = target.get_arg(4)
        target.read_register.assert_called_once_with("sp")
        target.read_memory_word.assert_called_once_with(0x1000)
        assert result == 0xCAFE

    def test_get_arg_stack_5(self):
        target = _make_mips_target()
        target.read_register.return_value = 0x1000  # sp
        target.read_memory_word.return_value = 0xF00D
        result = target.get_arg(5)
        target.read_register.assert_called_once_with("sp")
        target.read_memory_word.assert_called_once_with(0x1004)
        assert result == 0xF00D

    def test_get_arg_negative_raises(self):
        target = _make_mips_target()
        with pytest.raises(ValueError, match="Invalid arg index"):
            target.get_arg(-1)


class TestMIPSQemuTargetSetArg:
    def test_set_arg_register_0(self):
        target = _make_mips_target()
        target.set_arg(0, 42)
        target.write_register.assert_called_once_with("a0", 42)

    def test_set_arg_register_3(self):
        target = _make_mips_target()
        target.set_arg(3, 99)
        target.write_register.assert_called_once_with("a3", 99)

    def test_set_arg_stack_4(self):
        target = _make_mips_target()
        target.read_register.return_value = 0x2000  # sp
        target.set_arg(4, 0xABCD)
        target.read_register.assert_called_once_with("sp")
        target.write_memory_word.assert_called_once_with(0x2000, 0xABCD)

    def test_set_arg_stack_5(self):
        target = _make_mips_target()
        target.read_register.return_value = 0x2000  # sp
        target.set_arg(5, 0x1234)
        target.write_memory_word.assert_called_once_with(0x2004, 0x1234)

    def test_set_arg_negative_raises(self):
        target = _make_mips_target()
        with pytest.raises(ValueError):
            target.set_arg(-1, 0)


class TestMIPSQemuTargetExecuteReturn:
    def test_execute_return_sets_v0_and_pc(self):
        target = _make_mips_target()
        target.regs.ra = 0x8000
        target.execute_return(0x12345678)
        assert target.regs.v0 == 0x12345678
        assert target.regs.pc == 0x8000

    def test_execute_return_truncates_to_32_bits(self):
        target = _make_mips_target()
        target.regs.ra = 0x4000
        target.execute_return(0x1FFFFFFFF)
        assert target.regs.v0 == 0xFFFFFFFF
        assert target.regs.pc == 0x4000

    def test_execute_return_none_does_not_set_v0(self):
        target = _make_mips_target()
        target.regs.ra = 0x6000
        target.regs.v0 = 0x9999  # should not be overwritten
        target.execute_return(None)
        # v0 should not have been reassigned (the code skips the assignment)
        assert target.regs.pc == 0x6000

    def test_execute_return_zero(self):
        target = _make_mips_target()
        target.regs.ra = 0x7000
        target.execute_return(0)
        assert target.regs.v0 == 0
        assert target.regs.pc == 0x7000


class TestMIPSQemuTargetRetAddr:
    def test_get_ret_addr(self):
        target = _make_mips_target()
        target.regs.ra = 0xABCD
        assert target.get_ret_addr() == 0xABCD

    def test_set_ret_addr(self):
        target = _make_mips_target()
        target.set_ret_addr(0x9999)
        assert target.regs.ra == 0x9999
