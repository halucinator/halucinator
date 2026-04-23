"""Tests for halucinator.bp_handlers.generic.libc module."""

from unittest import mock

import pytest

from halucinator.bp_handlers.generic.libc import Libc6


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestPuts:
    def test_puts_reads_and_prints_string(self, qemu, capsys):
        handler = Libc6()
        qemu.get_arg.return_value = 0x5000
        qemu.read_string.return_value = "Hello World"

        intercept, ret = handler.puts(qemu, ADDR)

        qemu.read_string.assert_called_once_with(0x5000)
        assert intercept is True
        assert ret == 1
        captured = capsys.readouterr()
        assert "Hello World" in captured.out


class TestPrintf:
    def test_printf_no_format_specifiers(self, qemu, capsys):
        handler = Libc6()
        qemu.get_arg.side_effect = lambda i: 0x5000 if i == 0 else 0
        qemu.read_string.return_value = "plain text"

        intercept, ret = handler.printf(qemu, ADDR)

        assert intercept is True
        assert ret == len("plain text")
        captured = capsys.readouterr()
        assert "plain text" in captured.out

    def test_printf_with_int_format(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 42
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "value=%d"

        intercept, ret = handler.printf(qemu, ADDR)

        assert intercept is True
        captured = capsys.readouterr()
        assert "value=42" in captured.out

    def test_printf_with_hex_format(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 255
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "hex=%x"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        captured = capsys.readouterr()
        assert "hex=ff" in captured.out

    def test_printf_with_string_format(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0x6000
            return 0

        qemu.get_arg.side_effect = get_arg
        # First call for the format string, second for %s arg
        qemu.read_string.side_effect = ["msg=%s", "hello"]

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        captured = capsys.readouterr()
        assert "msg=hello" in captured.out

    def test_printf_with_unsigned_int(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 99
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%u"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        captured = capsys.readouterr()
        assert "val=99" in captured.out

    def test_printf_with_octal(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 8
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "oct=%o"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_unhandled_format(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%z"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        assert ret == 1
        captured = capsys.readouterr()
        assert "Unhandled format" in captured.out


class TestExit:
    def test_exit_calls_shutdown(self, qemu):
        handler = Libc6()
        qemu.get_arg.return_value = 0

        intercept, ret = handler.halucinator_exit(qemu, ADDR)

        qemu.halucinator_shutdown.assert_called_once_with(0)
        assert intercept is False
        assert ret is None

    def test_exit_masks_to_byte(self, qemu):
        handler = Libc6()
        qemu.get_arg.return_value = 0x1FF  # should mask to 0xFF

        handler.halucinator_exit(qemu, ADDR)

        qemu.halucinator_shutdown.assert_called_once_with(0xFF)


class TestPrintfFloatFormats:
    """Test float/double format specifiers in printf (lines 48, 56, 58, 62)."""

    def test_printf_with_float_f(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 3.14
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%f"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        captured = capsys.readouterr()
        assert "3.14" in captured.out

    def test_printf_with_float_e(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 1.5
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%e"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_float_g(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 2.5
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%g"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_float_a(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 1.0
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.return_value = "val=%a"

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True

    def test_printf_with_char_c(self, qemu, capsys):
        handler = Libc6()

        def get_arg(i):
            if i == 0:
                return 0x5000
            elif i == 1:
                return 0x6000
            return 0

        qemu.get_arg.side_effect = get_arg
        qemu.read_string.side_effect = ["char=%c", "A"]

        intercept, ret = handler.printf(qemu, ADDR)
        assert intercept is True
        captured = capsys.readouterr()
        assert "char=A" in captured.out
