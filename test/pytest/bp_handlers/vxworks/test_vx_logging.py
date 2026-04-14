"""Tests for halucinator.bp_handlers.vxworks.vx_logging"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.vx_logging import VxLogging, PRINTF_FORMAT_STR


class TestVxLogging:
    def test_init(self):
        vl = VxLogging()
        assert vl.log_msg_ptr is None

    def test_parse_printf_string_simple(self):
        vl = VxLogging()
        result = vl.parse_printf_string("Hello %s world %d")
        assert len(result) == 2
        assert result[0][1] == '%s'
        assert result[1][1] == '%d'

    def test_parse_printf_string_hex(self):
        vl = VxLogging()
        result = vl.parse_printf_string("addr: %x val: %08x")
        assert len(result) == 2
        assert '%x' in result[0][1]
        assert '%08x' in result[1][1]

    def test_parse_printf_string_no_formats(self):
        vl = VxLogging()
        result = vl.parse_printf_string("Hello world")
        assert len(result) == 0

    def test_parse_printf_string_escaped_percent(self):
        vl = VxLogging()
        result = vl.parse_printf_string("100%% done %d items")
        # Should find %% and %d
        types_found = [r[1] for r in result]
        assert '%%' in types_found
        assert '%d' in types_found

    def test_parse_printf_string_long_form(self):
        vl = VxLogging()
        result = vl.parse_printf_string("val: %ld addr: %lx")
        assert len(result) == 2

    def test_read_arg_supported_types(self):
        vl = VxLogging()
        # These should not raise
        for fmt in ['%%s', '%%i', '%%d', '%%x', '%%lx', '%%ld', '%%lu', '%%p', '%%u', '%%f']:
            vl.read_arg(0, fmt)

    def test_read_arg_unsupported_type(self):
        vl = VxLogging()
        with pytest.raises(TypeError, match="Unsupported format string type"):
            vl.read_arg(0, '%%q')

    def test_app_log(self, qemu):
        vl = VxLogging()
        def get_arg_side_effect(n):
            return [1, 0x2000, 0x10, 0x3000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(side_effect=["myLogger", "format string %s"])

        # app_log has no return value documented (returns None implicitly)
        result = vl.app_log(qemu, 0x1000)

        assert qemu.get_arg.call_count == 4
        assert qemu.read_string.call_count == 2

    def test_log_msg(self, qemu):
        vl = VxLogging()
        qemu.get_arg = mock.Mock(return_value=0x4000)
        qemu.read_string.return_value = "Hello World"

        result = vl.log_msg(qemu, 0x1000)

        assert result == (False, None)
        assert vl.log_msg_ptr == 0x4000
        qemu.get_arg.assert_called_with(0)
        qemu.read_string.assert_called_with(0x4000)

    def test_log_msg_updates_state(self, qemu):
        vl = VxLogging()
        qemu.get_arg = mock.Mock(return_value=0x5000)
        qemu.read_string.return_value = "Test Message"

        vl.log_msg(qemu, 0x1000)

        assert vl.log_msg_ptr == 0x5000
        assert vl.log_msg == "Test Message"
