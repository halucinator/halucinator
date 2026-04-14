from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.atmel_asf_v3.edbg_stub import EDBG_Stub

DUMMY_VALUE = 8888
PACKET_ADDRESS = 0x1000
PACKET_ADDRESS_WITH_OFFSET = 0x1002
assert PACKET_ADDRESS + 2 == PACKET_ADDRESS_WITH_OFFSET
PACKET_VALUE = bytes.fromhex("0800" "00200000")  # length  # address
PACKET_VALUE_ZERO_LEN = bytes.fromhex("0000" "00300000")  # length  # address
DEST_ADDRESS = 0x2000
DEST_ADDRESS_ZERO_LEN = 0x3000
EUI64_LEN = 8
DATA_TO_WRITE = "\55\55\55\55\55\55\55\55"
DEFAULT_EUI64 = ""
WORD_SIZE = 1
NUMBER_OF_WORDS = 6


@pytest.fixture
def edbg_stub():
    mock_model = mock.Mock()
    return EDBG_Stub(mock_model)


class TestEDBG_Stub:
    def test_return_void_just_returns_None(self, edbg_stub):
        # Associated HAL fuctions declaration
        # enum status_code
        # i2c_master_init (
        #   struct i2c_master_module *const module,
        #   I2c *const hw,
        #   const struct i2c_master_config *const config
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samb11/html/group__asfdoc__samb__i2c__group.html#ga1c146a1dc8b312f79af0b4af2351d9b5
        # static void
        # i2c_enable (
        #   I2c *const i2c_module
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samb11/html/group__asfdoc__samb__i2c__group.html#ga84020b375b908b46bcf77bf3bc75f47f
        continue_, retval = edbg_stub.return_void(None, None)
        assert continue_
        assert retval == None

    def test_return_ok_just_returns_zero(self, edbg_stub):
        # Associated HAL fuction declaration
        # enum status_code
        # i2c_master_write_packet_wait_no_stop (
        #   struct i2c_master_module *const module,
        #   struct i2c_master_packet *const packet
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samb11/html/group__asfdoc__samb__i2c__group.html#ga4d874599c2bff10cd08f5474041c66bb
        continue_, retval = edbg_stub.return_ok(None, None)
        assert continue_
        assert retval == 0

    def test_get_edbg_eui64_writes_eui64_to_qemu_memory_correctly_when_length_greater_than_length_of_eui64(
        self, edbg_stub, qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # i2c_master_read_packet_wait (
        #   struct i2c_master_module *const module,
        #   struct i2c_master_packet *const packet
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samb11/html/group__asfdoc__samb__i2c__group.html#ga238a755f972b9c3287131cda5fc25725
        set_arguments(qemu_mock, [DUMMY_VALUE, PACKET_ADDRESS])
        qemu_mock.read_memory = mock.Mock(return_value=PACKET_VALUE)
        qemu_mock.write_memory = mock.Mock()
        continue_, retval = edbg_stub.get_edbg_eui64(qemu_mock, None)
        assert continue_
        assert retval == 0
        qemu_mock.read_memory.assert_called_once_with(
            PACKET_ADDRESS_WITH_OFFSET, WORD_SIZE, NUMBER_OF_WORDS, raw=True
        )
        qemu_mock.write_memory.assert_called_once_with(
            DEST_ADDRESS, WORD_SIZE, DATA_TO_WRITE, EUI64_LEN, raw=True
        )

    def test_get_edbg_eui64_writes_eui64_to_qemu_memory_correctly_when_length_not_greater_than_length_of_eui64(
        self, edbg_stub, qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # i2c_master_read_packet_wait (
        #   struct i2c_master_module *const module,
        #   struct i2c_master_packet *const packet
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samb11/html/group__asfdoc__samb__i2c__group.html#ga238a755f972b9c3287131cda5fc25725
        set_arguments(qemu_mock, [DUMMY_VALUE, PACKET_ADDRESS])
        qemu_mock.read_memory = mock.Mock(return_value=PACKET_VALUE_ZERO_LEN)
        qemu_mock.write_memory = mock.Mock()
        continue_, retval = edbg_stub.get_edbg_eui64(qemu_mock, None)
        assert continue_
        assert retval == 0
        qemu_mock.read_memory.assert_called_once_with(
            PACKET_ADDRESS_WITH_OFFSET, WORD_SIZE, NUMBER_OF_WORDS, raw=True
        )
        qemu_mock.write_memory.assert_called_once_with(
            DEST_ADDRESS_ZERO_LEN, WORD_SIZE, DEFAULT_EUI64, 0, raw=True
        )
