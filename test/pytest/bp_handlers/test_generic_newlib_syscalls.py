"""Tests for halucinator.bp_handlers.generic.newlib_syscalls module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.newlib_syscalls import NewLibSysCalls


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestNewLibSysCalls:
    def test_write_prints_data(self, qemu, capsys):
        handler = NewLibSysCalls()

        def get_arg(i):
            if i == 1:
                return 0x5000  # buffer addr
            elif i == 2:
                return 5  # length
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_memory.return_value = b"Hello"

        intercept, ret = handler._write(qemu, ADDR)

        qemu.read_memory.assert_called_once_with(0x5000, 1, 5, raw=True)
        assert intercept is True
        assert ret == 5
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_write_returns_length(self, qemu, capsys):
        handler = NewLibSysCalls()

        qemu.get_arg.side_effect = lambda i: {1: 0x6000, 2: 3}.get(i, 0)
        qemu.read_memory.return_value = b"abc"

        intercept, ret = handler._write(qemu, ADDR)

        assert ret == 3

    def test_register_handler_finds_write(self):
        handler = NewLibSysCalls()
        qemu = mock.Mock()
        result = handler.register_handler(qemu, 0x1000, "_write")
        assert result is not None
