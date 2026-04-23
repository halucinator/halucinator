from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_tim import STM32_TIM


@pytest.fixture
def tim():
    mock_model = mock.Mock()
    return STM32_TIM(mock_model)


OBJ = 5
LEN = 4
SIZE = 1
TIM_BASE = 0x40000400
IRQ = 45
RATE = 2


class TestSTM32_TIM:
    def test_tim_init_calls_qemu_read_memory_and_just_returns_None(
        self, tim, qemu_mock
    ):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/9 is resolved
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Start_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#ga1b288eb68eb52c97b8d187cdd6e9088f
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.tim_init(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)

    @pytest.mark.xfail
    def test_deinit_calls_qemu_read_memory_and_just_crashes(
        self, tim, qemu_mock
    ):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/9 is resolved
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Start_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#gaaf97adbc39e48456a1c83c54895de83b
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.deinit(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)

    @pytest.mark.xfail
    def test_config_calls_qemu_read_memory_and_just_crashes(
        self, tim, qemu_mock
    ):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/9 is resolved
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_TIM_ConfigClockSource (
        #   TIM_HandleTypeDef * htim,
        #   TIM_ClockConfigTypeDef * sClockSourceConfig
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group8.html#ga43403d13849f71285ea1da3f3cb1381f
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.config(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)

    @pytest.mark.xfail
    def test_sync_calls_qemu_read_memory_and_just_crashes(
        self, tim, qemu_mock
    ):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/9 is resolved
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_TIMEx_MasterConfigSynchronization (
        #   TIM_HandleTypeDef * htim,
        #   TIM_MasterConfigTypeDef * sMasterConfig
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__timex__exported__functions__group5.html#ga056fd97d3be6c60dcfa12963f6ec8aad
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.sync(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)

    def test_start_starts_timer_correctly(self, tim, qemu_mock):
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Start_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#gae517d80e2ac713069767df8e8915971e
        tim.model.start_timer = mock.Mock()
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.start(qemu_mock, None)
        assert continue_
        assert ret_val is None
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)
        tim.model.start_timer.assert_called_with(hex(TIM_BASE), IRQ, RATE)

    def test_start_crashes_when_address_not_found(self, tim, qemu_mock):
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Start_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#gae517d80e2ac713069767df8e8915971e
        TIM1_BASE = 0x40000200
        tim.model.start_timer = mock.Mock()
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM1_BASE)
        with pytest.raises(KeyError):
            continue_, ret_val = tim.start(qemu_mock, None)
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)
        tim.model.start_timer.assert_not_called()

    def test_start_starts_timer_correctly_when_address_set_directly(
        self, tim, qemu_mock
    ):
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Start_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#gae517d80e2ac713069767df8e8915971e
        TIM1_BASE = 0x40000200
        IRQ1 = 40
        tim.addr2isr_lut = {TIM1_BASE: IRQ1}
        tim.model.start_timer = mock.Mock()
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM1_BASE)
        continue_, ret_val = tim.start(qemu_mock, None)
        assert continue_
        assert ret_val is None
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)
        tim.model.start_timer.assert_called_with(hex(TIM1_BASE), IRQ1, RATE)

    def test_isr_handler_sets_qemu_pc_register_correctly(self, tim, qemu_mock):
        # void
        # HAL_TIM_IRQHandler (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/stm32f4xx__hal__tim_8c_source.html#l02809
        CALLABLE_NAME = "HAL_TIM_PeriodElapsedCallback"
        CALLABLE_ADDRESS = 0x1000
        tim.model.start_timer = mock.Mock()
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        qemu_mock.avatar.callables = {CALLABLE_NAME: CALLABLE_ADDRESS}
        continue_, ret_val = tim.isr_handler(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS

    def test_stop_stops_timer_correctly(self, tim, qemu_mock):
        # HAL_StatusTypeDef
        # HAL_TIM_Base_Stop_IT (
        #   TIM_HandleTypeDef * htim
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__tim__exported__functions__group1.html#ga19443605c97f15b5ede7d8337534ece4
        tim.model.stop_timer = mock.Mock()
        set_arguments(qemu_mock, [OBJ])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.stop(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_called_with(OBJ, LEN, SIZE)
        tim.model.stop_timer.assert_called_with(hex(TIM_BASE))

    def test_sleep_just_returns_zero(self, tim, qemu_mock):
        # This function needs to be updated when https://gitlab.com/METIS/halucinator/-/issues/9 is resolved
        # Associated HAL fuction declaration
        # void
        # HAL_Delay (
        #   uint32_t Delay
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__hal__exported__functions__group2.html#gae63b34eea12780ca2e1100c2402da18e
        TIME_MS = 10000
        set_arguments(qemu_mock, [TIME_MS])
        qemu_mock.read_memory = mock.Mock(return_value=TIM_BASE)
        continue_, ret_val = tim.sleep(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_not_called()

    def test_systick_config_starts_timer_with_correct_rate_for_provided_irq(
        self, tim, qemu_mock
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
        tim.model.start_timer = mock.Mock()
        continue_, ret_val = tim.systick_config(None, None)
        assert continue_
        assert ret_val == 0
        tim.model.start_timer.assert_called_with(TIMER_NAME, IRQ, RATE)
