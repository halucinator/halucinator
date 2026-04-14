"""Tests for halucinator.bp_handlers.vxworks.posix_logging"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.posix_logging import PosixLogging


class TestPosixLogging:
    def test_creat(self, qemu):
        handler = PosixLogging()
        def get_arg_side_effect(n):
            return [0x2000, 0x644][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/tmp/test.txt")

        result = handler.creat(qemu, 0x1000)

        assert result == (False, None)
        qemu.read_string.assert_called_once_with(0x2000)

    def test_open(self, qemu):
        handler = PosixLogging()
        def get_arg_side_effect(n):
            return [0x2000, 0x02, 0x1A4][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/tmp/test.txt")

        result = handler.open(qemu, 0x1000)

        assert result == (False, None)
        qemu.read_string.assert_called_once_with(0x2000)
        # Should call get_arg for name, flags, mode
        assert qemu.get_arg.call_count == 3

    def test_mkdir(self, qemu):
        handler = PosixLogging()
        qemu.get_arg = mock.Mock(return_value=0x3000)
        qemu.read_string = mock.Mock(return_value="/tmp/mydir")

        result = handler.mkdir(qemu, 0x1000)

        assert result == (False, None)
        qemu.read_string.assert_called_once_with(0x3000)

    def test_x_delete(self, qemu):
        handler = PosixLogging()
        qemu.get_arg = mock.Mock(return_value=0x4000)
        qemu.read_string = mock.Mock(return_value="/tmp/deleteme.txt")

        result = handler.x_delete(qemu, 0x1000)

        assert result == (False, None)
        qemu.read_string.assert_called_once_with(0x4000)

    def test_all_handlers_return_false_none(self, qemu):
        """All PosixLogging handlers should be logging-only (False, None)."""
        handler = PosixLogging()
        qemu.get_arg = mock.Mock(return_value=0x2000)
        qemu.read_string.return_value = "test"

        for method_name in ['creat', 'open', 'mkdir', 'x_delete']:
            method = getattr(handler, method_name)
            result = method(qemu, 0x1000)
            assert result == (False, None), f"{method_name} should return (False, None)"
