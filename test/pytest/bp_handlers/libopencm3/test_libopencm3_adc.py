# Copyright 2022 GrammaTech Inc.
from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_adc import LIBOPENCM3_ADC

DATA_PTR1 = 0x1000
DATA_PTR2 = 0x2000
DATA_PTR3 = 0x3000
DATA_PTR4 = 0x4000
CHANNEL1 = 1
CHANNEL2 = 2
CHANNEL3 = 3
CHANNEL4 = 4
BUFFER_DATA1 = bytes([0x21])
BUFFER_DATA2 = bytes([0x41, 0x55])
BUFFER_DATA3 = bytes([0x30, 0x40, 0x50])
BUFFER_DATA4 = bytes([0x20, 0x45, 0x60, 0x77])
ENCODED_VALUE1 = 0x84000000
ENCODED_VALUE2 = 0x54100001
ENCODED_VALUE3 = 0x440C0002
ENCODED_VALUE4 = 0xDE116003

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    DATA_PTR1: [BUFFER_DATA1, 1, CHANNEL1, True],
    DATA_PTR2: [BUFFER_DATA2, 1, CHANNEL2, True],
    DATA_PTR3: [BUFFER_DATA3, 1, CHANNEL3, True],
    DATA_PTR4: [BUFFER_DATA4, 1, CHANNEL4, True],
}


@pytest.fixture
def adc_qemu(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def adc():
    mock_model = mock.Mock()
    return LIBOPENCM3_ADC(mock_model)


class TestLIBOPENCM3_ADC:
    def test_hal_ok_just_returns_zero(self, adc):
        # Associated HAL functions declaration can be found at
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c
        continue_, retval = adc.hal_ok(None, None)
        assert continue_
        assert retval == 0

    def test_hal_power_on_just_returns_zero(self, adc):
        # Associated HAL functions declaration
        # void
        # adc_power_on (
        #   uint32_t adc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c#L107
        continue_, retval = adc.hal_power_on(None, None)
        assert continue_
        assert retval == 0

    def test_hal_power_off_just_returns_zero(self, adc):
        # Associated HAL functions declaration
        # void
        # adc_power_off (
        #   uint32_t adc
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/common/adc_common_v1.c#L108
        continue_, retval = adc.hal_power_off(None, None)
        assert continue_
        assert retval == 0

    def test_hal_calibrating_just_returns_false(self, adc):
        # Associated HAL functions declaration
        # bool
        # adc_is_calibrating (
        #   uint32_t adc,
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/504dc95d9ba1c2505a30d575371accfe49a69fb9/lib/stm32/f1/adc.c#L363
        continue_, retval = adc.hal_calibrating(None, None)
        assert continue_
        assert not retval

    @pytest.mark.parametrize("channel", [-10, -1, 0, 5, 10, 100])
    def test_hal_read_injected_returns_zero_and_does_not_call_adc_read_when_channel_incorrect(
        self, adc_qemu, adc, channel
    ):
        # Associated HAL functions declaration
        # uint32_t
        # adc_read_injected (
        #   uint32_t adc,
        #   uint8_t reg
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L483
        adc.model.adc_read = mock.Mock(return_value=1)
        set_arguments(adc_qemu, [0, channel])
        continue_, retval = adc.hal_read_injected(adc_qemu, None)
        assert continue_
        assert retval == 0
        adc.model.adc_read.assert_not_called()

    @pytest.mark.parametrize(
        "channel, value", [(1, 22), (2, 33), (3, 100), (4, 777)]
    )
    def test_hal_read_injected_returns_correct_value_when_channel_correct(
        self, adc_qemu, adc, channel, value
    ):
        # Associated HAL functions declaration
        # uint32_t
        # adc_read_injected (
        #   uint32_t adc,
        #   uint8_t reg
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L483
        adc.model.adc_read = mock.Mock(return_value=value)
        set_arguments(adc_qemu, [0, channel])
        continue_, retval = adc.hal_read_injected(adc_qemu, None)
        assert continue_
        assert retval == value
        adc.model.adc_read.assert_called_once_with(channel)

    @pytest.mark.parametrize("channel", [-10, -1, 0, 5, 10, 100])
    def test_hal_set_injected_does_not_call_adc_write_when_channel_incorrect(
        self, adc_qemu, adc, channel
    ):
        # Associated HAL functions declaration
        # void
        # adc_set_injected_sequence (
        #   uint32_t adc,
        #   uint8_t length,
        #   uint8_t channel[]
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L630
        adc.model.adc_read = mock.Mock(return_value=1)
        set_arguments(adc_qemu, [0, channel])
        continue_, retval = adc.hal_set_injected(adc_qemu, None)
        assert continue_
        assert retval == 0
        adc.model.adc_write.assert_not_called()

    @pytest.mark.parametrize(
        "channel, data_ptr, encoded",
        [
            (CHANNEL1, DATA_PTR1, ENCODED_VALUE1),
            (CHANNEL2, DATA_PTR2, ENCODED_VALUE2),
            (CHANNEL3, DATA_PTR3, ENCODED_VALUE3),
            (CHANNEL4, DATA_PTR4, ENCODED_VALUE4),
        ],
    )
    def test_hal_set_injected_calls_adc_write_and_sends_encoded_data_when_channel_correct(
        self, adc_qemu, adc, channel, data_ptr, encoded
    ):
        # Associated HAL functions declaration
        # void
        # adc_set_injected_sequence (
        #   uint32_t adc,
        #   uint8_t length,
        #   uint8_t channel[]
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/adc_common_v1.c#L630
        adc.model.adc_read = mock.Mock(return_value=1)
        set_arguments(adc_qemu, [0, channel, data_ptr])
        continue_, retval = adc.hal_set_injected(adc_qemu, None)
        assert continue_
        assert retval == 0
        adc.model.adc_write.assert_called_once_with(1, encoded)
