"""Tests for halucinator.bp_handlers.vxworks.sys_clock"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.sys_clock import SysClock


class TestSysClock:
    def test_init_defaults(self):
        clk = SysClock(irq_num=5)
        assert clk.irq_num == 5
        assert clk.name == 'sysClk'
        assert clk.scale == 10
        assert clk.rate == 1
        assert clk.delay == 0

    def test_init_custom(self):
        clk = SysClock(irq_num=7, name='myClk', scale=20, rate=5, delay=100)
        assert clk.irq_num == 7
        assert clk.name == 'myClk'
        assert clk.scale == 20
        assert clk.rate == 5
        assert clk.delay == 100

    def test_sys_clk_enable(self, qemu):
        clk = SysClock(irq_num=5, name='testClk', rate=0.1, delay=10)
        clk.model = mock.Mock()

        result = clk.sys_clk_enable(qemu, 0x1000)

        assert result == (False, 0)
        clk.model.start_timer.assert_called_once_with('testClk', 5, 0.1, 10)

    def test_sys_clock_rate_set(self, qemu):
        clk = SysClock(irq_num=5, scale=10)
        qemu.get_arg = mock.Mock(return_value=100)  # 100 ticks/sec

        result = clk.sys_clock_rate_set(qemu, 0x1000)

        assert result == (False, None)
        # rate = (1.0/100) * 10 = 0.1
        assert clk.rate == pytest.approx(0.1)

    def test_sys_clock_rate_set_different_scale(self, qemu):
        clk = SysClock(irq_num=5, scale=1)
        qemu.get_arg = mock.Mock(return_value=50)

        clk.sys_clock_rate_set(qemu, 0x1000)

        assert clk.rate == pytest.approx(0.02)

    def test_sys_clk_disable(self, qemu):
        clk = SysClock(irq_num=5, name='testClk')
        clk.model = mock.Mock()

        result = clk.sys_clk_disable(qemu, 0x1000)

        assert result == (False, None)
        clk.model.stop_timer.assert_called_once_with('testClk')
