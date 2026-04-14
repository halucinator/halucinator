"""Tests for halucinator.bp_handlers.generic.basic_io module."""

import struct
from unittest import mock

import pytest

from halucinator.bp_handlers.generic.basic_io import BasicIO


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestReadDigital:
    def test_read_digital(self, qemu):
        handler = BasicIO()
        handler.digital_model = mock.Mock()
        handler.digital_model.get_value.return_value = 1

        qemu.get_arg.side_effect = [0x10, 0x2000]  # channel_id, ret_ptr

        intercept, ret = handler.read_digital(qemu, ADDR)

        handler.digital_model.get_value.assert_called_once_with(0x10)
        qemu.write_memory.assert_called_once_with(0x2000, 1, 1)
        assert intercept is True
        assert ret == 0


class TestWriteDigital:
    def test_write_digital(self, qemu):
        handler = BasicIO()
        handler.digital_model = mock.Mock()

        qemu.get_arg.side_effect = [0x10, 1]  # channel_id, value

        intercept, ret = handler.write_digital(qemu, ADDR)

        handler.digital_model.set_value.assert_called_once_with(0x10, 1)
        assert intercept is True
        assert ret == 0


class TestReadAnalog:
    def test_read_analog(self, qemu):
        handler = BasicIO()
        handler.analog_model = mock.Mock()
        handler.analog_model.get_value.return_value = 3.14

        qemu.get_arg.side_effect = [0x10, 0x3000]

        intercept, ret = handler.read_analog(qemu, ADDR)

        handler.analog_model.get_value.assert_called_once_with(0x10)
        expected_data = struct.pack("<f", 3.14)
        qemu.write_memory.assert_called_once_with(0x3000, 4, expected_data, raw=True)
        assert intercept is True
        assert ret == 0


class TestWriteAnalog:
    def test_write_analog(self, qemu):
        handler = BasicIO()
        handler.analog_model = mock.Mock()

        float_val = 2.5
        int_repr = struct.unpack("<I", struct.pack("<f", float_val))[0]
        qemu.get_arg.side_effect = [0x10, int_repr]

        intercept, ret = handler.write_analog(qemu, ADDR)

        call_args = handler.analog_model.set_value.call_args[0]
        assert call_args[0] == 0x10
        assert abs(call_args[1] - 2.5) < 0.001
        assert intercept is True
        assert ret == 0
