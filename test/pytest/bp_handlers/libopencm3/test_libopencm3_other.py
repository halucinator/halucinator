# Copyright 2022 GrammaTech Inc.
import pytest

from halucinator.bp_handlers.libopencm3.libopencm3_other import (
    LIBOPENCM3_Other,
)


@pytest.fixture
def other():
    return LIBOPENCM3_Other()


class TestLIBOPENCM3_Other:
    def test_hal_ok_just_returns_zero(self, other):
        # Associated HAL functions declaration can be found here -
        # exti_set_trigger - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L82
        # exti_enable_request - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L83
        # exti_reset_request - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L85
        # exti_get_flag_status - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L87
        # exti_select_source - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/stm32/common/exti_common_all.h#L86
        # null_handler - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/cm3/vector.c#L37
        # reset_handler - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L167
        # scb_reset_system - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/scb.h#L556
        # nvic_enable_irq - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L153
        # nvic_set_priority - https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/include/libopencm3/cm3/nvic.h#L159
        continue_, retval = other.hal_ok(None, None)
        assert continue_
        assert retval == 0
