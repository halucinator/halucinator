from unittest import mock
from unittest.mock import patch

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_base import STM32F4_Base


@pytest.fixture
def base():
    mock_model = mock.Mock()
    return STM32F4_Base(mock_model)


class TestSTM32F4_Base:
    def test_init_just_returns_False_and_None(self, base):
        # HAL_StatusTypeDef HAL_Init(void)
        continue_, ret_val = base.init(None, None)
        assert not continue_
        assert ret_val is None

    def test_hal_systeminit_just_returns_False_and_None(self, base):
        continue_, ret_val = base.systeminit(None, None)
        assert not continue_
        assert ret_val is None

    def test_hal_systemclock_config_just_returns_True_and_Zero(self, base):
        continue_, ret_val = base.systemclock_config(None, None)
        assert continue_
        assert ret_val == 0

    def test_hal_rcc_osc_config_just_returns_True_and_Zero(self, base):
        continue_, ret_val = base.rcc_osc_config(None, None)
        assert continue_
        assert ret_val == 0

    def test_hal_rcc_clock_config_just_returns_True_and_Zero(self, base):
        continue_, ret_val = base.rcc_clock_config(None, None)
        assert continue_
        assert ret_val == 0

    def test_systick_config_starts_timer_with_correct_rate_for_provided_irq(
        self, base, qemu_mock
    ):
        # uint32_t HAL_SYSTICK_Config (uint32_t	TicksNumb)
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__cortex__exported__functions__group1.html#gac3a3f0d53c315523a8e6e7bcac1940cf
        # Both rate and irq hardcoded in the function
        # When the function is changed to use the rate value provide via r0
        # the instance of qemu with correctly set r0 should be passed to systick_config
        RATE = 5
        IRQ = 15
        TIMER_NAME = "SysTick"
        base.model.start_timer = mock.Mock()
        continue_, ret_val = base.systick_config(None, None)
        assert continue_
        assert ret_val == 0
        base.model.start_timer.assert_called_with(TIMER_NAME, IRQ, RATE)

    def test_systick_clksourceconfig_just_returns_None(self, base, qemu_mock):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/6 is resolved
        # Associated HAL fuction declaration
        # void HAL_SYSTICK_CLKSourceConfig	(uint32_t CLKSource)
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__cortex__exported__functions__group2.html#ga3284dc8428996f5b6aa6b3b99e643788
        set_arguments(qemu_mock, [0])
        continue_, ret_val = base.systick_clksourceconfig(qemu_mock, None)
        assert not continue_
        assert ret_val is None

    def test_init_tick_just_returns_zero(self, base):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/7 is resolved
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef HAL_InitTick ( uint32_t TickPriority)
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__hal__exported__functions__group1.html#ga879cdb21ef051eb81ec51c18147397d5
        continue_, ret_val = base.init_tick(None, None)
        assert continue_
        assert ret_val == 0

    def test_error_handler_stops_2_timers_and_calls_set_trace(self, base):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/8 is resolved
        TIMER1 = "SysTick"
        TIMER2 = "0x40000400"
        base.model.stop_timer = mock.Mock()
        # Ensure ipdb is importable (mock it if not installed)
        ipdb_mock = mock.MagicMock()
        with patch.dict("sys.modules", {"ipdb": ipdb_mock}):
            continue_, ret_val = base.error_handler(None, None)
        assert continue_
        assert ret_val == 0
        assert base.model.stop_timer.call_count == 2
        ipdb_mock.set_trace.assert_called_once()
