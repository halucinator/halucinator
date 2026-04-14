"""Tests for halucinator.bp_handlers.generic.common module."""

import time
from unittest import mock

import pytest

from halucinator.bp_handlers.generic.common import (
    Canary,
    KillExit,
    MovePC,
    PrintChar,
    PrintString,
    ReturnConstant,
    ReturnZero,
    SetMemory,
    SetRegisters,
    SkipFunc,
    SleepTime,
)


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


# ---------------------------------------------------------------------------
# SleepTime
# ---------------------------------------------------------------------------


class TestSleepTime:
    def test_register_handler(self, qemu):
        handler = SleepTime()
        result = handler.register_handler(qemu, ADDR, "my_sleep", sleep_time=5)
        assert handler.sleep_times[ADDR] == 5

    def test_sleep_time_handler(self, qemu):
        handler = SleepTime()
        handler.sleep_times[ADDR] = 0  # zero sleep for fast test
        with mock.patch("halucinator.bp_handlers.generic.common.time.sleep") as mock_sleep:
            intercept, ret = handler.sleep_time(qemu, ADDR)
        mock_sleep.assert_called_once_with(0)
        assert intercept is False
        assert ret == 0


# ---------------------------------------------------------------------------
# ReturnZero
# ---------------------------------------------------------------------------


class TestReturnZero:
    def test_register_handler(self, qemu):
        handler = ReturnZero()
        result = handler.register_handler(qemu, ADDR, "my_func", silent=True)
        assert handler.silent[ADDR] is True
        assert handler.func_names[ADDR] == "my_func"

    def test_return_zero_handler(self, qemu):
        handler = ReturnZero()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "test_func"
        intercept, ret = handler.return_zero(qemu, ADDR)
        assert intercept is True
        assert ret == 0

    def test_return_zero_silent(self, qemu):
        handler = ReturnZero()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "test_func"
        intercept, ret = handler.return_zero(qemu, ADDR)
        assert intercept is True
        assert ret == 0


# ---------------------------------------------------------------------------
# ReturnConstant
# ---------------------------------------------------------------------------


class TestReturnConstant:
    def test_register_handler(self, qemu):
        handler = ReturnConstant()
        handler.register_handler(qemu, ADDR, "my_func", ret_value=0x42, silent=True)
        assert handler.ret_values[ADDR] == 0x42
        assert handler.func_names[ADDR] == "my_func"

    def test_return_constant_handler(self, qemu):
        handler = ReturnConstant()
        handler.ret_values[ADDR] = 0x42
        handler.silent[ADDR] = True  # silent to avoid log formatting
        handler.func_names[ADDR] = "test"
        intercept, ret = handler.return_constant(qemu, ADDR)
        assert intercept is True
        assert ret == 0x42

    def test_return_constant_not_silent(self, qemu):
        handler = ReturnConstant()
        handler.ret_values[ADDR] = 0x10
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "func"
        intercept, ret = handler.return_constant(qemu, ADDR)
        assert intercept is True
        assert ret == 0x10


# ---------------------------------------------------------------------------
# Canary
# ---------------------------------------------------------------------------


class TestCanary:
    def test_register_handler(self, qemu):
        handler = Canary()
        handler.register_handler(
            qemu, ADDR, "canary_func", canary_type="StackOverflow", msg="oops"
        )
        assert handler.func_names[ADDR] == "canary_func"
        assert handler.canary_type[ADDR] == "StackOverflow"
        assert handler.msg[ADDR] == "oops"

    def test_handle_canary(self, qemu):
        handler = Canary()
        handler.func_names[ADDR] = "func"
        handler.canary_type[ADDR] = "BufferOverflow"
        handler.msg[ADDR] = "detected"
        handler.model = mock.Mock()

        intercept, ret = handler.handle_canary(qemu, ADDR)
        handler.model.canary.assert_called_once_with(
            qemu, ADDR, "BufferOverflow", "detected"
        )
        assert intercept is True
        assert ret == 0


# ---------------------------------------------------------------------------
# PrintChar
# ---------------------------------------------------------------------------


class TestPrintChar:
    def test_register_handler(self, qemu):
        handler = PrintChar()
        handler.register_handler(
            qemu, ADDR, "putchar", silent=False, intercept=True
        )
        assert handler.func_names[ADDR] == "putchar"
        assert handler.silent[ADDR] is False
        assert handler.intercept[ADDR] is True

    def test_put_char_intercept(self, qemu):
        handler = PrintChar()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "putchar"
        handler.intercept[ADDR] = True
        qemu.get_arg.return_value = ord("A")
        qemu.get_ret_addr.return_value = 0x2000

        intercept, ret = handler.put_char(qemu, ADDR)
        assert intercept is True
        assert ret is None

    def test_put_char_no_intercept(self, qemu):
        handler = PrintChar()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "putchar"
        handler.intercept[ADDR] = False
        qemu.get_arg.return_value = ord("B")
        qemu.get_ret_addr.return_value = 0x2000

        intercept, ret = handler.put_char(qemu, ADDR)
        assert intercept is False
        assert ret is None

    def test_put_char_not_silent(self, qemu):
        handler = PrintChar()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "putchar"
        handler.intercept[ADDR] = True
        qemu.get_arg.return_value = ord("X")
        qemu.get_ret_addr.return_value = 0x3000

        intercept, ret = handler.put_char(qemu, ADDR)
        assert intercept is True


# ---------------------------------------------------------------------------
# PrintString
# ---------------------------------------------------------------------------


class TestPrintString:
    def test_register_handler(self, qemu):
        handler = PrintString()
        handler.register_handler(
            qemu, ADDR, "puts", arg_num=1, max_len=128, silent=False, intercept=True
        )
        assert handler.arg_num[ADDR] == 1
        assert handler.max_len[ADDR] == 128
        assert handler.intercept[ADDR] is True

    def test_print_string_intercept(self, qemu):
        handler = PrintString()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "puts"
        handler.arg_num[ADDR] = 0
        handler.max_len[ADDR] = 256
        handler.intercept[ADDR] = True
        qemu.get_arg.return_value = 0x5000
        qemu.read_string.return_value = "hello"
        qemu.get_ret_addr.return_value = 0x2000

        intercept, ret = handler.print_string(qemu, ADDR)
        assert intercept is True
        assert ret is None

    def test_print_string_no_intercept(self, qemu):
        handler = PrintString()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "puts"
        handler.arg_num[ADDR] = 0
        handler.max_len[ADDR] = 256
        handler.intercept[ADDR] = False

        intercept, ret = handler.print_string(qemu, ADDR)
        assert intercept is False
        assert ret is None

    def test_print_string_not_silent(self, qemu):
        handler = PrintString()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "puts"
        handler.arg_num[ADDR] = 0
        handler.max_len[ADDR] = 256
        handler.intercept[ADDR] = True
        qemu.get_arg.return_value = 0x5000
        qemu.read_string.return_value = "world"
        qemu.get_ret_addr.return_value = 0x2000

        intercept, ret = handler.print_string(qemu, ADDR)
        assert intercept is True


# ---------------------------------------------------------------------------
# SkipFunc
# ---------------------------------------------------------------------------


class TestSkipFunc:
    def test_register_handler(self, qemu):
        handler = SkipFunc()
        handler.register_handler(qemu, ADDR, "skip_me", silent=True)
        assert handler.silent[ADDR] is True
        assert handler.func_names[ADDR] == "skip_me"

    def test_skip_handler(self, qemu):
        handler = SkipFunc()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "skip_me"
        intercept, ret = handler.skip(qemu, ADDR)
        assert intercept is True
        assert ret is None

    def test_skip_handler_silent(self, qemu):
        handler = SkipFunc()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "skip_me"
        intercept, ret = handler.skip(qemu, ADDR)
        assert intercept is True
        assert ret is None


# ---------------------------------------------------------------------------
# MovePC
# ---------------------------------------------------------------------------


class TestMovePC:
    def test_register_handler(self, qemu):
        handler = MovePC()
        handler.register_handler(qemu, ADDR, "move_func", move_by=8, silent=False)
        assert handler.move_pc_amount[ADDR] == 8
        assert handler.silent[ADDR] is False

    def test_move_pc_handler(self, qemu):
        handler = MovePC()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "move"
        handler.move_pc_amount[ADDR] = 4
        qemu.regs.pc = 0x1000

        intercept, ret = handler.move_pc(qemu, ADDR)
        assert qemu.regs.pc == 0x1004
        assert intercept is False
        assert ret is None

    def test_move_pc_not_silent(self, qemu):
        handler = MovePC()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "move"
        handler.move_pc_amount[ADDR] = 8
        qemu.regs.pc = 0x2000

        intercept, ret = handler.move_pc(qemu, ADDR)
        assert qemu.regs.pc == 0x2008


# ---------------------------------------------------------------------------
# KillExit
# ---------------------------------------------------------------------------


class TestKillExit:
    def test_register_handler(self, qemu):
        handler = KillExit()
        handler.register_handler(qemu, ADDR, "exit", exit_code=1, silent=False)
        assert handler.exit_status[ADDR] == 1
        assert handler.silent[ADDR] is False

    def test_kill_and_exit(self, qemu):
        handler = KillExit()
        handler.silent[ADDR] = True
        handler.func_names[ADDR] = "exit"
        handler.exit_status[ADDR] = 0

        intercept, ret = handler.kill_and_exit(qemu, ADDR)
        qemu.halucinator_shutdown.assert_called_once_with(0)
        assert intercept is False
        assert ret is None

    def test_kill_and_exit_not_silent(self, qemu):
        handler = KillExit()
        handler.silent[ADDR] = False
        handler.func_names[ADDR] = "exit"
        handler.exit_status[ADDR] = 1

        intercept, ret = handler.kill_and_exit(qemu, ADDR)
        qemu.halucinator_shutdown.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# SetRegisters
# ---------------------------------------------------------------------------


class TestSetRegisters:
    def test_register_handler(self, qemu):
        handler = SetRegisters()
        handler.register_handler(
            qemu, ADDR, "set_regs", registers={"r0": 0xFF}, silent=False
        )
        assert handler.changes[ADDR] == {"r0": 0xFF}

    def test_set_registers_handler(self, qemu):
        handler = SetRegisters()
        handler.changes[ADDR] = {"r0": 0x10, "r1": 0x20}
        handler.silent[ADDR] = True

        intercept, ret = handler.set_registers(qemu, ADDR)
        assert qemu.write_register.call_count == 2
        qemu.write_register.assert_any_call("r0", 0x10)
        qemu.write_register.assert_any_call("r1", 0x20)
        assert intercept is False
        assert ret == 0


# ---------------------------------------------------------------------------
# SetMemory
# ---------------------------------------------------------------------------


class TestSetMemory:
    def test_register_handler(self, qemu):
        handler = SetMemory()
        handler.register_handler(
            qemu, ADDR, "set_mem", addresses={0x2000: 0xAB}, silent=False
        )
        assert handler.changes[ADDR] == {0x2000: 0xAB}

    def test_set_memory_handler(self, qemu):
        handler = SetMemory()
        handler.changes[ADDR] = {0x2000: 0xAA, 0x3000: 0xBB}
        handler.silent[ADDR] = True

        intercept, ret = handler.set_memory(qemu, ADDR)
        assert qemu.write_memory.call_count == 2
        qemu.write_memory.assert_any_call(0x2000, 4, 0xAA)
        qemu.write_memory.assert_any_call(0x3000, 4, 0xBB)
        assert intercept is False
        assert ret == 0
