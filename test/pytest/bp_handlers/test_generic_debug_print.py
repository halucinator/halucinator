"""Tests for halucinator.bp_handlers.generic.debug_print module."""

from typing import cast
from unittest import mock

import pytest

from halucinator.bp_handlers.generic.debug_print import DebugPrint


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestDebugPrint:
    def test_register_handler_defaults(self, qemu):
        handler = DebugPrint()
        result = handler.register_handler(qemu, ADDR, "debug_func")
        assert handler.argument[ADDR] == 1
        assert handler.prefix[ADDR] == ""

    def test_register_handler_custom(self, qemu):
        handler = DebugPrint()
        handler.register_handler(qemu, ADDR, "debug_func", argument=2, prefix="[DBG] ")
        assert handler.argument[ADDR] == 2
        assert handler.prefix[ADDR] == "[DBG] "

    def test_register_handler_argument_too_large(self, qemu):
        handler = DebugPrint()
        with pytest.raises(ValueError, match="Argument limited"):
            handler.register_handler(qemu, ADDR, "debug_func", argument=5)

    def test_output_reads_string_and_returns(self, qemu):
        handler = DebugPrint()
        handler.qemu = qemu
        handler.argument[ADDR] = 1
        handler.prefix[ADDR] = ""
        qemu.get_arg.return_value = 0x5000
        # Simulate reading a null-terminated string
        qemu.read_memory.return_value = b"Hello\x00" + b"\x00" * 74

        intercept, ret = handler.output(qemu, ADDR)
        assert intercept is True
        assert ret == 0
        qemu.get_arg.assert_called_once_with(0)  # argument 1 - 1 = 0

    def test_output_with_prefix(self, qemu):
        handler = DebugPrint()
        handler.qemu = qemu
        handler.argument[ADDR] = 2
        handler.prefix[ADDR] = "[TEST] "
        qemu.get_arg.return_value = 0x5000
        qemu.read_memory.return_value = b"msg\x00" + b"\x00" * 76

        intercept, ret = handler.output(qemu, ADDR)
        assert intercept is True
        assert ret == 0
        qemu.get_arg.assert_called_once_with(1)  # argument 2 - 1 = 1

    def test_output_handles_exception(self, qemu):
        handler = DebugPrint()
        handler.qemu = qemu
        handler.argument[ADDR] = 1
        handler.prefix[ADDR] = ""
        qemu.get_arg.return_value = 0x5000
        qemu.read_memory.side_effect = Exception("read error")

        # Should not raise, just log the exception
        intercept, ret = handler.output(qemu, ADDR)
        assert intercept is True
        assert ret == 0

    def test_output_with_no_null_terminator(self, qemu):
        handler = DebugPrint()
        handler.qemu = qemu
        handler.argument[ADDR] = 1
        handler.prefix[ADDR] = ""
        qemu.get_arg.return_value = 0x5000
        # No null terminator in the data
        qemu.read_memory.return_value = b"A" * 80

        intercept, ret = handler.output(qemu, ADDR)
        assert intercept is True
        assert ret == 0
