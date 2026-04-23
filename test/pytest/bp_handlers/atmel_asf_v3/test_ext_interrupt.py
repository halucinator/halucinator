from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.ext_interrupt import EXT_Int

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    0: [0],
}

CHANNEL1 = 0
CHANNEL2 = 1
CHANNEL3 = 2
CHANNEL4 = 3
CHANNEL5 = 4
CHANNEL6 = 5
CHANNEL_NAME1 = "Channel1"
CHANNEL_NAME2 = "Channel2"
CHANNEL_NAME3 = "Channel3"
CHANNEL_NAME4 = "Channel4"
CHANNEL_NAME5 = "Channel5"
CHANNEL_NAME6 = "Channel6"

CHANNELS_MAP = {
    CHANNEL1: CHANNEL_NAME1,
    CHANNEL2: CHANNEL_NAME2,
    CHANNEL3: CHANNEL_NAME3,
    CHANNEL4: CHANNEL_NAME4,
    CHANNEL5: CHANNEL_NAME5,
    CHANNEL6: CHANNEL_NAME6,
}

CHANNELS = {
    # Parameters and return value structure for channel is_active function
    # <channel_name>: <active - True or False>
    CHANNEL_NAME1: True,
    CHANNEL_NAME2: False,
    CHANNEL_NAME3: False,
    CHANNEL_NAME4: True,
    CHANNEL_NAME5: True,
    CHANNEL_NAME6: False,
}
CHANNEL_BIT_MAP = 25
VALUE = 12


def create_is_active_fake(channels):
    def is_active_fake(channel_name):
        return channels.get(channel_name, mock.DEFAULT)

    return is_active_fake


@pytest.fixture
def sd_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def ext_interrupt():
    mock_model = mock.Mock()
    ext_int = EXT_Int(mock_model)
    ext_int.model.is_active.side_effect = create_is_active_fake(CHANNELS)
    return ext_int


class TestEXT_Int:
    def test_register_callback_just_returns_None(
        self, ext_interrupt, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # extint_register_callback (
        #   const extint_callback_t callback,
        #   const uint8_t channel,
        #   const enum extint_callback_type type
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.asf_programmers_manual.atsaml21/html/group__asfdoc__sam0__extint__group.html#gae1bedc3fd379b3dd62f88efb17a2758f
        set_arguments(sd_qemu_mock, [2])  # Set something to pass it to the log
        continue_, ret_val = ext_interrupt.register_callback(
            sd_qemu_mock, None
        )
        assert not continue_
        assert ret_val == None

    @pytest.mark.xfail
    @pytest.mark.parametrize("address", [0x40001800, 0x40001FFF])
    def test_avatar_read_memory_in_specific_range_just_returns_zero(
        self, ext_interrupt, address
    ):
        SIZE = 0x20
        ret = ext_interrupt.read_memory(address, SIZE)
        assert ret == 0

    @pytest.mark.xfail
    def test_avatar_read_memory_in_specific_range_with_offset_0x10_and_no_channels_just_returns_zero(
        self, ext_interrupt
    ):
        ADDRESS = 0x40001810
        SIZE = 0x20
        ext_interrupt.channel_map = {}  # No channels
        ret = ext_interrupt.read_memory(ADDRESS, SIZE)
        assert ret == 0

    @pytest.mark.xfail
    def test_avatar_read_memory_in_specific_range_with_offset_0x10_and_channels_returns_bit_map_of_active_channels_in_reverse_order(
        self, ext_interrupt
    ):
        ADDRESS = 0x40001810
        SIZE = 0x20
        ext_interrupt.channel_map = CHANNELS_MAP
        ret = ext_interrupt.read_memory(ADDRESS, SIZE)
        assert ret == CHANNEL_BIT_MAP

    @pytest.mark.parametrize(
        "address", [0x2000, 0x400017FF, 0x40002000, 0x62002000]
    )
    def test_avatar_read_memory_out_of_specific_range_causes_exception(
        self, ext_interrupt, address
    ):
        SIZE = 0x1
        with pytest.raises(Exception):
            ret = ext_interrupt.read_memory(address, SIZE)
            assert ret == 0

    @pytest.mark.xfail
    @pytest.mark.parametrize("address", [0x40001800, 0x40001FFF])
    def test_avatar_write_memory_in_specific_range_just_returns_True(
        self, ext_interrupt, address
    ):
        SIZE = 0x20
        ret = ext_interrupt.write_memory(address, SIZE, VALUE)
        assert ret == True

    @pytest.mark.xfail
    def test_avatar_write_memory_in_specific_range_with_offset_0x08_and_no_channels_does_not_call_clear_active(
        self, ext_interrupt
    ):
        ADDRESS = 0x40001808
        SIZE = 0x20
        ext_interrupt.channel_map = {}  # No channels
        ext_interrupt.model.clear_active = mock.Mock()
        ret = ext_interrupt.write_memory(ADDRESS, SIZE, VALUE)
        assert ret == True
        ext_interrupt.model.clear_active.assert_not_called()

    @pytest.mark.xfail
    def test_avatar_write_memory_in_specific_range_with_offset_0x08_calls_clear_active_for_active_channels(
        self, ext_interrupt
    ):
        ADDRESS = 0x40001808
        SIZE = 0x20
        ext_interrupt.channel_map = CHANNELS_MAP
        ext_interrupt.model.clear_active = mock.Mock()
        ret = ext_interrupt.write_memory(ADDRESS, SIZE, VALUE)
        assert ret == True
        ext_interrupt.model.clear_active.assert_any_call(CHANNEL_NAME1)
        ext_interrupt.model.clear_active.assert_any_call(CHANNEL_NAME4)
        ext_interrupt.model.clear_active.assert_any_call(CHANNEL_NAME5)

    @pytest.mark.parametrize(
        "address", [0x2000, 0x400017FF, 0x40002000, 0x62002000]
    )
    def test_avatar_write_memory_out_of_specific_range_causes_exception(
        self, ext_interrupt, address
    ):
        SIZE = 0x1
        with pytest.raises(Exception):
            ret = ext_interrupt.write_memory(address, SIZE, VALUE)
            assert ret == True
