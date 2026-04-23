"""Tests for halucinator.bp_handlers.vxworks.vx_mem"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.vx_mem import VxMem, BColors


class TestBColors:
    def test_color_codes(self):
        assert '\033[' in BColors.OKBLUE
        assert '\033[' in BColors.OKGREEN
        assert BColors.ENDC == '\033[0m'


class TestVxMem:
    def test_vx_mem_probe_read_mode(self, qemu):
        handler = VxMem()

        def get_arg_side_effect(n):
            # adrs=0x1000, mode=0(READ), length=4, pVal=0x2000
            return [0x1000, 0, 4, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(return_value=b'\x01\x02\x03\x04')

        result = handler.vx_mem_probe(qemu, 0x5000)

        assert result == (True, 0)
        qemu.read_memory.assert_called_once_with(0x1000, 4, 1)
        qemu.write_memory.assert_called_once_with(0x2000, 1, b'\x01\x02\x03\x04', 4, raw=True)

    def test_vx_mem_probe_write_mode(self, qemu):
        handler = VxMem()

        def get_arg_side_effect(n):
            # adrs=0x1000, mode=1(WRITE), length=4, pVal=0x2000
            return [0x1000, 1, 4, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(return_value=b'\xAA\xBB\xCC\xDD')

        result = handler.vx_mem_probe(qemu, 0x5000)

        assert result == (True, 0)
        qemu.read_memory.assert_called_once_with(0x2000, 4, 1)
        qemu.write_memory.assert_called_once_with(0x1000, 1, b'\xAA\xBB\xCC\xDD', 4, raw=True)

    def test_vx_mem_probe_invalid_mode(self, qemu):
        handler = VxMem()

        def get_arg_side_effect(n):
            # adrs=0x1000, mode=3(INVALID), length=4, pVal=0x2000
            return [0x1000, 3, 4, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.vx_mem_probe(qemu, 0x5000)

        assert result == (True, 0)
        qemu.read_memory.assert_not_called()
        qemu.write_memory.assert_not_called()

    def test_vx_mem_probe_read_exception(self, qemu):
        handler = VxMem()

        def get_arg_side_effect(n):
            return [0x1000, 0, 4, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        exc = Exception("read error")
        exc.message = "read error"
        qemu.read_memory = mock.Mock(side_effect=exc)

        # Should not raise, just log warning
        result = handler.vx_mem_probe(qemu, 0x5000)
        assert result == (True, 0)

    def test_vx_mem_probe_write_exception(self, qemu):
        handler = VxMem()

        def get_arg_side_effect(n):
            return [0x1000, 1, 4, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        exc = Exception("write error")
        exc.message = "write error"
        qemu.read_memory = mock.Mock(side_effect=exc)

        result = handler.vx_mem_probe(qemu, 0x5000)
        assert result == (True, 0)

    def test_vx_mem_probe_different_lengths(self, qemu):
        handler = VxMem()

        for length in [1, 2, 4, 8]:
            def get_arg_side_effect(n, l=length):
                return [0x1000, 0, l, 0x2000][n]
            qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
            qemu.read_memory = mock.Mock(return_value=b'\x00' * length)

            result = handler.vx_mem_probe(qemu, 0x5000)
            assert result == (True, 0)
            qemu.read_memory.assert_called_once_with(0x1000, length, 1)
