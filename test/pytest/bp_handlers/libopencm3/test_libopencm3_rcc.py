# Copyright 2022 GrammaTech Inc.
from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_rcc import LIBOPENCM3_RCC


@pytest.fixture
def qemu():
    mock_model = mock.Mock()
    return mock_model


@pytest.fixture
def rcc():
    mock_model = mock.Mock()
    return LIBOPENCM3_RCC(mock_model)


class TestLIBOPENCM3_RCC:
    @pytest.mark.parametrize("osc", [1, 2, 4, 5, 10])
    def test_hal_osc_on_just_returns_zero(self, qemu, rcc, osc):
        # Associated HAL function declaration
        # void
        # rcc_osc_on (
        #   enum rcc_osc osc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c#L244
        set_arguments(qemu, [osc])
        continue_, retval = rcc.hal_osc_on(qemu, None)
        assert continue_
        assert retval == 0

    @pytest.mark.parametrize("osc", [1, 2, 4, 5, 10])
    def test_hal_osc_ready_just_returns_true(self, qemu, rcc, osc):
        # Associated HAL function declaration
        # bool
        # rcc_is_osc_ready (
        #   enum rcc_osc osc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c#L206
        set_arguments(qemu, [osc])
        continue_, retval = rcc.hal_osc_ready(qemu, None)
        assert continue_
        assert retval

    def test_hal_ok_just_returns_zero(self, rcc):
        # Associated HAL functions declaration can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f0/rcc.c
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/rcc.c
        continue_, retval = rcc.hal_ok(None, None)
        assert continue_
        assert retval == 0
