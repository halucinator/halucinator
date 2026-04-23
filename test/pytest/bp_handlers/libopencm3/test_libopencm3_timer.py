# Copyright 2022 GrammaTech Inc.
import time

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_timer import (
    LIBOPENCM3_Timer,
)


@pytest.fixture
def timer():
    return LIBOPENCM3_Timer()


class TestLIBOPENCM3_Timer:
    def test_hal_ok_just_returns_zero(self, timer):
        # Associated HAL function declaration
        # void
        # timer_set_master_mode (
        #   uint32_t timer_peripheral,
        #   uint32_t mode
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L533
        continue_, retval = timer.hal_ok(None, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize("div", [1, 2, 4, 5, 10])
    def test_hal_timer_set_mode_sets_timer_data_correctly(
        self, qemu_mock, timer, timer_id, div
    ):
        # Associated HAL function declaration
        # void
        # timer_set_clock_division (
        #   uint32_t timer_peripheral,
        #   uint32_t clock_div,
        #   uint32_t alignment,
        #   uint32_t direction
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L237
        set_arguments(qemu_mock, [timer_id, div, 1, 1])
        continue_, retval = timer.hal_timer_set_mode(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.start_time[timer_id] == 0.0
        assert timer.clock_div[timer_id] == div
        assert timer.period[timer_id] == 1

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize("div", [1, 2, 4, 5, 10])
    def test_hal_timer_set_clock_division_sets_division_correctly(
        self, qemu_mock, timer, timer_id, div
    ):
        # Associated HAL function declaration
        # void
        # timer_set_clock_division (
        #   uint32_t timer_peripheral,
        #   uint32_t clock_div
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L263
        set_arguments(qemu_mock, [timer_id, div])
        continue_, retval = timer.hal_timer_set_clock_division(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.clock_div[timer_id] == div

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize("div", [1, 2, 4, 5, 10])
    def test_hal_timer_set_prescaler_sets_division_correctly(
        self, qemu_mock, timer, timer_id, div
    ):
        # Associated HAL function declaration
        # void
        # timer_set_prescaler (
        #   uint32_t timer_peripheral,
        #   uint32_t value
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L650
        set_arguments(qemu_mock, [timer_id, div])
        continue_, retval = timer.hal_timer_set_prescaler(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.clock_div[timer_id] == div + 1

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    def test_hal_timer_enable_counter_starts_timer(
        self, qemu_mock, timer, timer_id
    ):
        # Associated HAL function declaration
        # void
        # timer_enable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L435
        set_arguments(qemu_mock, [timer_id])
        cur_time = time.time()
        continue_, retval = timer.hal_timer_enable_counter(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.start_time[timer_id] >= cur_time

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    def test_hal_timer_enable_counter_sets_division_and_period_to_one_when_they_not_set(
        self, qemu_mock, timer, timer_id
    ):
        # Associated HAL function declaration
        # void
        # timer_enable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L435
        set_arguments(qemu_mock, [timer_id])
        timer.clock_div = {}
        timer.period = {}
        cur_time = time.time()
        continue_, retval = timer.hal_timer_enable_counter(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.start_time[timer_id] >= cur_time
        assert timer.clock_div[timer_id] == 1
        assert timer.period[timer_id] == 1

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize("div_value", [2, 4, 8])
    @pytest.mark.parametrize("period_value", [10, 20, 64])
    def test_hal_timer_enable_counter_does_not_set_division_and_period_to_one_when_they_are_set(
        self, qemu_mock, timer, timer_id, div_value, period_value
    ):
        # Associated HAL function declaration
        # void
        # timer_enable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L435
        set_arguments(qemu_mock, [timer_id])
        timer.clock_div[timer_id] = div_value
        timer.period[timer_id] = period_value
        cur_time = time.time()
        continue_, retval = timer.hal_timer_enable_counter(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.start_time[timer_id] >= cur_time
        assert timer.clock_div[timer_id] == div_value
        assert timer.period[timer_id] == period_value

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    def test_hal_timer_disable_counter_stops_timer(
        self, qemu_mock, timer, timer_id
    ):
        # Associated HAL function declaration
        # void
        # timer_disable_counter (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L447
        set_arguments(qemu_mock, [timer_id])
        continue_, retval = timer.hal_timer_disable_counter(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.start_time[timer_id] == 0.0

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize("period", [10, 20, 32, 64])
    def test_hal_timer_set_period_sets_period_correctly(
        self, qemu_mock, timer, timer_id, period
    ):
        # Associated HAL function declaration
        # void
        # timer_set_period (
        #   uint32_t timer_peripheral,
        #   uint32_t period
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/timer_common_all.c#L683
        set_arguments(qemu_mock, [timer_id, period])
        continue_, retval = timer.hal_timer_set_period(qemu_mock, None)
        assert continue_
        assert retval == 0
        assert timer.period[timer_id] == period

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    def test_hal_timer_get_count_returns_zero_for_stopped_timer(
        self, qemu_mock, timer, timer_id
    ):
        # Associated HAL function declaration
        # int
        # timer_get_count (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/d8aa2f17b02d1ae8e6c3cb9f1f64f1d8aaea4f4b/lib/stm32/common/timer_common_all.c#L82
        set_arguments(qemu_mock, [timer_id])
        timer.start_time[timer_id] = 0.0
        continue_, retval = timer.hal_timer_get_count(qemu_mock, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    def test_hal_timer_get_count_returns_zero_for_non_existing_timer(
        self, qemu_mock, timer, timer_id
    ):
        # Associated HAL function declaration
        # int
        # timer_get_count (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/d8aa2f17b02d1ae8e6c3cb9f1f64f1d8aaea4f4b/lib/stm32/common/timer_common_all.c#L82
        set_arguments(qemu_mock, [timer_id])
        timer.start_time = {}
        continue_, retval = timer.hal_timer_get_count(qemu_mock, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("timer_id", [1, 5, 10, 100])
    @pytest.mark.parametrize(
        "div, period, delay, hits",
        [
            (1, 1, 0.1, 100),
            (10, 1, 1, 100),
            (1, 5, 0.25, 50),
            (4, 5, 0.2, 10),
            (4, 8, 0.075, 2),
            (4, 8, 0.016, 0),
            (15, 20, 0.305, 1),
        ],
    )
    def test_timer_get_count_returns_number_of_timer_hits(
        self, qemu_mock, timer, timer_id, div, period, delay, hits
    ):
        # Associated HAL function declaration
        # int
        # timer_get_count (
        #   uint32_t timer_peripheral
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/d8aa2f17b02d1ae8e6c3cb9f1f64f1d8aaea4f4b/lib/stm32/common/timer_common_all.c#L82
        set_arguments(qemu_mock, [timer_id, div, 1, 1])
        continue_, retval = timer.hal_timer_set_mode(qemu_mock, None)
        assert continue_
        assert retval == 0
        set_arguments(qemu_mock, [timer_id, period])
        continue_, retval = timer.hal_timer_set_period(qemu_mock, None)
        assert continue_
        assert retval == 0
        set_arguments(qemu_mock, [timer_id])
        continue_, retval = timer.hal_timer_enable_counter(qemu_mock, None)
        assert continue_
        assert retval == 0
        time.sleep(delay)
        set_arguments(qemu_mock, [timer_id])
        continue_, retval = timer.hal_timer_get_count(qemu_mock, None)
        assert continue_
        assert retval == hits
