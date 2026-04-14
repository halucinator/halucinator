"""Tests for halucinator.bp_handlers.mbed.{serial,boot,timer} modules."""

import struct
from unittest import mock

import pytest

from halucinator.bp_handlers.mbed.boot import MbedBoot
from halucinator.bp_handlers.mbed.serial import MbedUART
from halucinator.bp_handlers.mbed.timer import MbedTimer


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


# ---------------------------------------------------------------------------
# MbedBoot
# ---------------------------------------------------------------------------


class TestMbedBoot:
    def test_system_init(self, qemu):
        handler = MbedBoot()
        qemu.regs.lr = 0x2000
        intercept, ret = handler.SystemInit(qemu, ADDR)
        assert intercept is True
        assert ret is None

    def test_mbed_sdk_init(self, qemu):
        handler = MbedBoot()
        intercept, ret = handler.mbed_sdk_init(qemu, ADDR)
        assert intercept is True
        assert ret is None

    def test_software_init_hook(self, qemu):
        handler = MbedBoot()
        intercept, ret = handler.software_init_hook(qemu, ADDR)
        assert intercept is True
        assert ret == 0

    def test_software_init_hook_rtos(self, qemu):
        handler = MbedBoot()
        intercept, ret = handler.software_init_hook_rtos(qemu, ADDR)
        assert intercept is True
        assert ret == 0

    def test_register_handler_finds_SystemInit(self):
        handler = MbedBoot()
        result = handler.register_handler(mock.Mock(), ADDR, "SystemInit")
        assert result is not None

    def test_register_handler_finds_mbed_sdk_init(self):
        handler = MbedBoot()
        result = handler.register_handler(mock.Mock(), ADDR, "mbed_sdk_init")
        assert result is not None

    def test_register_handler_finds_software_init_hook(self):
        handler = MbedBoot()
        result = handler.register_handler(mock.Mock(), ADDR, "software_init_hook")
        assert result is not None

    def test_register_handler_finds_software_init_hook_rtos(self):
        handler = MbedBoot()
        result = handler.register_handler(mock.Mock(), ADDR, "software_init_hook_rtos")
        assert result is not None


# ---------------------------------------------------------------------------
# MbedUART
# ---------------------------------------------------------------------------


class TestMbedUART:
    def test_getc(self, qemu):
        model = mock.Mock()
        model.read.return_value = [b"A"]
        handler = MbedUART(impl=model)

        qemu.regs.r0 = 0x100
        qemu.regs.r1 = 0x200

        intercept, ret = handler.getc(qemu, ADDR)
        model.read.assert_called_once_with(0x100, 1, block=True)
        assert intercept is True
        assert ret == ord(b"A")

    def test_putc(self, qemu):
        model = mock.Mock()
        handler = MbedUART(impl=model)

        qemu.regs.r0 = 0x100
        qemu.regs.r1 = ord("X")

        intercept, ret = handler.putc(qemu, ADDR)
        model.write.assert_called_once_with(0x100, "X")
        assert intercept is True
        assert ret == 1

    def test_puts(self, qemu):
        model = mock.Mock()
        handler = MbedUART(impl=model)

        qemu.regs.r0 = 0x100
        qemu.regs.r1 = 0x5000
        # Simulate reading chars then null
        qemu.read_memory.side_effect = [ord("H"), ord("i"), "\x00"]

        intercept, ret = handler.puts(qemu, ADDR)
        assert intercept is True
        assert ret == 3  # H, i, \x00

    def test_register_handler_finds_getc(self):
        handler = MbedUART()
        result = handler.register_handler(
            mock.Mock(), ADDR, "_ZN4mbed6Stream4getcEv"
        )
        assert result is not None

    def test_register_handler_finds_putc(self):
        handler = MbedUART()
        result = handler.register_handler(
            mock.Mock(), ADDR, "_ZN4mbed6Stream4putcEv"
        )
        assert result is not None

    def test_register_handler_finds_puts(self):
        handler = MbedUART()
        result = handler.register_handler(
            mock.Mock(), ADDR, "_ZN4mbed6Stream4putsEPKc"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# MbedTimer
# ---------------------------------------------------------------------------


class TestMbedTimer:
    def test_wait(self, qemu):
        handler = MbedTimer()
        # Encode 0.0 as float -> int
        float_as_int = struct.unpack("<I", struct.pack("<f", 0.0))[0]
        qemu.regs.r0 = float_as_int

        with mock.patch("halucinator.bp_handlers.mbed.timer.sleep") as mock_sleep:
            intercept, ret = handler.wait(qemu, ADDR)

        mock_sleep.assert_called_once_with(0.0)
        assert intercept is False
        assert ret == 0

    def test_wait_with_value(self, qemu):
        handler = MbedTimer()
        float_as_int = struct.unpack("<I", struct.pack("<f", 1.5))[0]
        qemu.regs.r0 = float_as_int

        with mock.patch("halucinator.bp_handlers.mbed.timer.sleep") as mock_sleep:
            intercept, ret = handler.wait(qemu, ADDR)

        args = mock_sleep.call_args[0]
        assert abs(args[0] - 1.5) < 0.01

    def test_register_handler_finds_wait(self):
        handler = MbedTimer()
        result = handler.register_handler(mock.Mock(), ADDR, "wait")
        assert result is not None
