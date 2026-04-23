"""Tests for halucinator.bp_handlers.generic.debug module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.debug import (
    CortexMDebugHelper,
    IPythonShell,
)


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


# ---------------------------------------------------------------------------
# IPythonShell
# ---------------------------------------------------------------------------


class TestIPythonShell:
    def test_register_handler_no_ignore(self, qemu):
        handler = IPythonShell()
        result = handler.register_handler(qemu, ADDR, "debug_func")
        assert handler.addr2name[ADDR] == "debug_func"
        assert handler.ignore_list[ADDR] == []

    def test_register_handler_with_ignore(self, qemu):
        handler = IPythonShell()
        handler.register_handler(qemu, ADDR, "debug_func", ignore=[0x2000, "sym"])
        assert handler.ignore_list[ADDR] == [0x2000, "sym"]

    def test_start_shell_skips_when_pc_in_ignore_list(self, qemu):
        handler = IPythonShell()
        handler.addr2name[ADDR] = "debug"
        handler.ignore_list[ADDR] = [0x2000]
        qemu.regs.pc = 0x2000

        intercept, ret = handler.start_shell(qemu, ADDR)
        assert intercept is False
        assert ret is None

    def test_start_shell_skips_when_symbol_in_ignore_list(self, qemu):
        handler = IPythonShell()
        handler.addr2name[ADDR] = "debug"
        handler.ignore_list[ADDR] = ["my_symbol"]
        qemu.regs.pc = 0x3000
        qemu.get_symbol_name.return_value = "my_symbol"

        intercept, ret = handler.start_shell(qemu, ADDR)
        assert intercept is False
        assert ret is None

    def test_start_shell_opens_ipython_when_not_ignored(self, qemu):
        handler = IPythonShell()
        handler.addr2name[ADDR] = "debug"
        handler.ignore_list[ADDR] = []

        with mock.patch("halucinator.bp_handlers.generic.debug.IPython.embed"):
            with mock.patch("halucinator.bp_handlers.generic.debug.system"):
                intercept, ret = handler.start_shell(qemu, ADDR)

        assert intercept is False
        assert ret is None

    def test_print_helpers(self, capsys):
        handler = IPythonShell()
        handler.print_helpers()
        captured = capsys.readouterr()
        assert "CortexMDebugHelper" in captured.out


# ---------------------------------------------------------------------------
# CortexMDebugHelper
# ---------------------------------------------------------------------------


class TestCortexMDebugHelper:
    def test_get_mem(self, qemu):
        qemu.read_memory.return_value = 0xDEADBEEF
        helper = CortexMDebugHelper(qemu)
        result = helper.get_mem(0x2000)
        qemu.read_memory.assert_called_with(0x2000, 4, 1)
        assert result == 0xDEADBEEF

    def test_get_stacked_pc(self, qemu):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x08001234
        helper = CortexMDebugHelper(qemu)
        result = helper.get_stacked_pc(0)
        # PC is at offset 4*6 = 24 from sp
        qemu.read_memory.assert_called_with(0x20000000 + 24, 4, 1)

    def test_get_stacked_pc_with_offset(self, qemu):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x08001234
        helper = CortexMDebugHelper(qemu)
        result = helper.get_stacked_pc(8)
        qemu.read_memory.assert_called_with(0x20000000 + 24 + 8, 4, 1)

    def test_print_exception_stack(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x42
        helper = CortexMDebugHelper(qemu)
        helper.print_exception_stack(0)
        captured = capsys.readouterr()
        assert "R0:" in captured.out
        assert "PC:" in captured.out
        assert "LR:" in captured.out
        assert "xPSR:" in captured.out

    def test_parse_cfsr_memmanage(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        # Test with bit 7 set (MMAR valid)
        helper.parse_cfsr(1 << 7, 0)
        captured = capsys.readouterr()
        assert "MemManage Fault Address Valid" in captured.out

    def test_parse_cfsr_busfault_addr_valid(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 15, 0)
        captured = capsys.readouterr()
        assert "Bus Fault Addr Valid" in captured.out

    def test_parse_cfsr_div_by_zero(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << (9 + 16), 0)
        captured = capsys.readouterr()
        assert "Div by zero" in captured.out

    def test_parse_cfsr_data_access(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 1, 0)
        captured = capsys.readouterr()
        assert "Data Access" in captured.out

    def test_parse_cfsr_instruction_access(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1, 0)
        captured = capsys.readouterr()
        assert "Instruction Access Violation" in captured.out

    def test_parse_cfsr_stacking_fault(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 4, 0)
        captured = capsys.readouterr()
        assert "Stacking for an exception" in captured.out

    def test_parse_cfsr_unstacking_fault(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 3, 0)
        captured = capsys.readouterr()
        assert "Unstacking" in captured.out

    def test_parse_cfsr_fp_lazy(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 5, 0)
        captured = capsys.readouterr()
        assert "floating-point" in captured.out

    def test_parse_cfsr_bus_stacking(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 12, 0)
        captured = capsys.readouterr()
        assert "Exception Stacking fault" in captured.out

    def test_parse_cfsr_bus_unstacking(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 11, 0)
        captured = capsys.readouterr()
        assert "Exception UnStacking fault" in captured.out

    def test_parse_cfsr_imprecise_bus(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 10, 0)
        captured = capsys.readouterr()
        assert "Imprecise data bus error" in captured.out

    def test_parse_cfsr_precise_bus(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 9, 0)
        captured = capsys.readouterr()
        assert "Precise data bus error" in captured.out

    def test_parse_cfsr_instruction_bus(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 8, 0)
        captured = capsys.readouterr()
        assert "Instruction bus error" in captured.out

    def test_parse_cfsr_bus_during(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 13, 0)
        captured = capsys.readouterr()
        assert "bus fault occurred during" in captured.out

    def test_parse_cfsr_unaligned(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << (8 + 16), 0)
        captured = capsys.readouterr()
        assert "Unaligned" in captured.out

    def test_parse_cfsr_no_coprocessor(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << (3 + 16), 0)
        captured = capsys.readouterr()
        assert "No Coprocessor" in captured.out

    def test_parse_cfsr_invalid_pc(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << (2 + 16), 0)
        captured = capsys.readouterr()
        assert "Invalid PC load" in captured.out

    def test_parse_cfsr_invalid_state(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << (1 + 16), 0)
        captured = capsys.readouterr()
        assert "Invalid state" in captured.out

    def test_parse_cfsr_undefined_instruction(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_cfsr(1 << 16, 0)
        captured = capsys.readouterr()
        assert "Undefined instruction" in captured.out

    def test_parse_hardfault_forced(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_hardfault(1 << 30, 0)
        captured = capsys.readouterr()
        assert "Forced" in captured.out

    def test_parse_hardfault_bus(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.parse_hardfault(1 << 1, 0)
        captured = capsys.readouterr()
        assert "Bus Fault" in captured.out

    def test_print_hardfault_info(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.print_hardfault_info(0)
        captured = capsys.readouterr()
        assert "Configurable Fault Status Reg" in captured.out

    def test_hf_alias(self, qemu, capsys):
        qemu.regs.sp = 0x20000000
        qemu.read_memory.return_value = 0x00
        helper = CortexMDebugHelper(qemu)
        helper.hf(0)
        captured = capsys.readouterr()
        assert "Configurable Fault Status Reg" in captured.out
