"""Tests for halucinator.bp_handlers.generic.timer module."""

import time
from unittest import mock

import pytest

from halucinator.bp_handlers.generic.timer import Timer


@pytest.fixture
def qemu():
    m = mock.Mock()
    m.regs = mock.Mock()
    return m


ADDR = 0x1000


class TestTimer:
    def test_register_handler_default_scale(self, qemu):
        timer = Timer()
        handler = timer.register_handler(qemu, ADDR, "my_timer")
        assert timer.scale[ADDR] == 1
        assert ADDR in timer.start_time
        assert handler is Timer.get_value

    def test_register_handler_custom_scale(self, qemu):
        timer = Timer()
        handler = timer.register_handler(qemu, ADDR, "my_timer", scale=10)
        assert timer.scale[ADDR] == 10

    def test_get_value_returns_time_ms(self, qemu):
        timer = Timer()
        timer.scale[ADDR] = 1
        timer.start_time[ADDR] = time.time() - 1.0  # 1 second ago

        intercept, ret = timer.get_value(qemu, ADDR)
        assert intercept is True
        # Should be roughly 1000ms (with some tolerance)
        assert 900 <= ret <= 1200

    def test_get_value_with_scale(self, qemu):
        timer = Timer()
        timer.scale[ADDR] = 2
        timer.start_time[ADDR] = time.time() - 1.0

        intercept, ret = timer.get_value(qemu, ADDR)
        assert intercept is True
        # 1000ms / 2 scale = ~500ms
        assert 400 <= ret <= 700

    def test_get_value_zero_elapsed(self, qemu):
        timer = Timer()
        timer.scale[ADDR] = 1
        timer.start_time[ADDR] = time.time()

        intercept, ret = timer.get_value(qemu, ADDR)
        assert intercept is True
        assert ret >= 0
