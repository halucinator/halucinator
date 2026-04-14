from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake

from halucinator.bp_handlers.atmel_asf_v3.hle_ethernet import Ksz8851HLE

LR_RESET_VALUE = 0xFFFFFFFF

ID = "ksz8851"

# netif Offsets
NETIF_STATE = 32
NETIF_INPUT = 16

# struct ksz8851snl_device Offsets
NUM_RX_BUFFS = 2
NUM_TX_BUFFS = 2

DEVICE_RX_DESC = 0
DEVICE_TX_DESC = 4 * NUM_RX_BUFFS
DEVICE_RX_PBUF = DEVICE_TX_DESC + (4 * NUM_TX_BUFFS)
DEVICE_TX_PBUF = DEVICE_RX_PBUF + (4 * NUM_RX_BUFFS)
DEVICE_RX_HEAD = DEVICE_TX_PBUF + (4 * NUM_TX_BUFFS)
DEVICE_RX_TAIL = DEVICE_RX_HEAD + 4
DEVICE_TX_HEAD = DEVICE_RX_TAIL + 4
DEVICE_TX_TAIL = DEVICE_TX_HEAD + 4
DEVICE_NETIF = DEVICE_TX_TAIL + 4

# pbuf offsets
PBUF_NEXT = 0
PBUF_PAYLOAD = 4
PBUF_TOT_LEN = 8
PBUF_LEN = 10
PBUF_TYPE = 12
PBUF_FLAGS = 13
PBUF_REF = 14

# Ethernet Types
ETHTYPE_ARP = 0x0806
ETHTYPE_IP = 0x0800

PADDING = 2  # Padding used on ethernet frames to keep alignment

ZERO_FRAMES = ZERO_FRAME_SIZE = ZERO_POINTER = 0
WORD_SIZE = 4
SHORT_WORD_SIZE = 2
ONE_WORD = 1
NUMBER_OF_FRAMES = 2
SIZE_1ST_FRAME = 16
FRAME_DATA = b"\x25" * SIZE_1ST_FRAME
FRAME_DATA_0X0806 = bytes.fromhex("0102030405060708090a0b0c08060f10")
FRAME_DATA_0X0800 = bytes.fromhex("112233445566778899aabbcc08000fEE")

BUFFER_ADDRESS = 0x1500

DEV_PTR_FOR_ZERO_MEMORY_READ = 0x2400
NETIF_PTR_FOR_ZERO_MEMORY_READ = 0x2600

DEV_PTR = 0x3200
NETIF_PTR = 0x3400
RX_BUF_PTR = 0x3600
PAYLOAD_PTR = 0x3800
INPUT_FN_PTR = 0x3900

BUF_TO_SEND_PTR = 0x420000
BUF_TO_SEND_PTR_NEXT = 0x440000
PAYLOAD_PART1_PTR = 0x600000
PAYLOAD_PART2_PTR = 0x640000
FRAME_PART1_DATA_TO_SEND = bytes.fromhex("21324354657687")
FRAME_PART1_LEN_TO_SEND = len(FRAME_PART1_DATA_TO_SEND)
FRAME_PART2_DATA_TO_SEND = bytes.fromhex("98A9BaCbDc08000f26")
FRAME_PART2_LEN_TO_SEND = len(FRAME_PART2_DATA_TO_SEND)

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    DEV_PTR_FOR_ZERO_MEMORY_READ
    + DEVICE_RX_PBUF: [ZERO_POINTER, WORD_SIZE, ONE_WORD],
    NETIF_PTR_FOR_ZERO_MEMORY_READ
    + NETIF_STATE: [DEV_PTR_FOR_ZERO_MEMORY_READ, WORD_SIZE, ONE_WORD],
    DEV_PTR + DEVICE_RX_PBUF: [RX_BUF_PTR, WORD_SIZE, ONE_WORD],
    NETIF_PTR + NETIF_STATE: [DEV_PTR, WORD_SIZE, ONE_WORD],
    RX_BUF_PTR + PBUF_PAYLOAD: [PAYLOAD_PTR, WORD_SIZE, ONE_WORD],
    NETIF_PTR + NETIF_INPUT: [INPUT_FN_PTR, WORD_SIZE, ONE_WORD],
    BUF_TO_SEND_PTR
    + PBUF_LEN: [FRAME_PART1_LEN_TO_SEND + PADDING, SHORT_WORD_SIZE, ONE_WORD],
    BUF_TO_SEND_PTR + PBUF_PAYLOAD: [PAYLOAD_PART1_PTR, WORD_SIZE, ONE_WORD],
    PAYLOAD_PART1_PTR
    + PADDING: [
        FRAME_PART1_DATA_TO_SEND,
        1,
        FRAME_PART1_LEN_TO_SEND,
        True,
    ],  # Only the first part has padding
    BUF_TO_SEND_PTR + PBUF_NEXT: [BUF_TO_SEND_PTR_NEXT, WORD_SIZE, ONE_WORD],
    BUF_TO_SEND_PTR_NEXT
    + PBUF_LEN: [FRAME_PART2_LEN_TO_SEND, SHORT_WORD_SIZE, ONE_WORD],
    BUF_TO_SEND_PTR_NEXT
    + PBUF_PAYLOAD: [PAYLOAD_PART2_PTR, WORD_SIZE, ONE_WORD],
    PAYLOAD_PART2_PTR: [
        FRAME_PART2_DATA_TO_SEND,
        1,
        FRAME_PART2_LEN_TO_SEND,
        True,
    ],
    BUF_TO_SEND_PTR_NEXT + PBUF_NEXT: [ZERO_POINTER, WORD_SIZE, ONE_WORD],
}

NETIF_PTR_INIT = 0x19000
ORG_LR_INIT = 0x20000
QEMU_LR_INIT = 0x30000
QEMU_R0_INIT = 0x31000
QEMU_PC_INIT = 0x32000
RX_POPULATE_QUEUE_ADDRESS = 0x40000
AVATAR_CALLABLES = {
    "ksz8851snl_rx_populate_queue": RX_POPULATE_QUEUE_ADDRESS,
}


@pytest.fixture
def hle_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    qemu_mock.regs.lr = LR_RESET_VALUE
    return qemu_mock


@pytest.fixture
def hle():
    mock_model = mock.Mock()
    ksz8851 = Ksz8851HLE(mock_model)
    return ksz8851


class TestKsz8851Eth:
    def test_sys_get_ms_just_returns_zero(self, hle):
        # Associated HAL fuctions declaration
        # static void
        # ksz8851snl_hard_reset (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/ksz8851snl_8c.html#ac6fdf4f9c0fae7ed84362b04f4e8dfe2
        # static void
        # ksz8851snl_interface_init (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/ksz8851snl_8c.html#a5d7f5c7036fcc40f364a7499a0281e68
        continue_, ret_val = hle.sys_get_ms(None, None)
        assert continue_
        assert ret_val == 0

    def test_ethernetif_input_returns_None_when_no_frames_available(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ethernetif_input (
        #   struct netif * netif
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b594ba7163978faa6f67288054079ac
        hle.dev_ptr = DEV_PTR_FOR_ZERO_MEMORY_READ
        hle.netif_ptr = NETIF_PTR_INIT
        hle.model.get_frame_info = mock.Mock(
            return_value=(ZERO_FRAMES, ZERO_FRAME_SIZE)
        )
        continue_, ret_val = hle.ethernetif_input(hle_qemu_mock, None)
        assert continue_
        assert ret_val is None
        hle.model.get_frame_info.assert_called_once_with(ID)
        assert hle.dev_ptr is None
        assert hle.netif_ptr is None

    def test_ethernetif_input_populates_queue_and_returns_None_when_rx_pbuf_ptr_equals_zero(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ethernetif_input (
        #   struct netif * netif
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b594ba7163978faa6f67288054079ac
        hle_qemu_mock.avatar.callables = AVATAR_CALLABLES
        hle_qemu_mock.regs.lr = QEMU_LR_INIT
        hle_qemu_mock.regs.r0 = QEMU_R0_INIT
        hle_qemu_mock.regs.pc = QEMU_PC_INIT
        hle.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        hle.netif_ptr = NETIF_PTR_INIT
        hle.dev_ptr = DEV_PTR_FOR_ZERO_MEMORY_READ
        hle.org_lr = ORG_LR_INIT
        continue_, ret_val = hle.ethernetif_input(hle_qemu_mock, None)
        assert not continue_
        assert ret_val is None
        hle.model.get_frame_info.assert_called_once_with(ID)
        assert hle.org_lr == ORG_LR_INIT
        assert hle_qemu_mock.regs.r0 == DEV_PTR_FOR_ZERO_MEMORY_READ
        assert hle_qemu_mock.regs.lr == QEMU_PC_INIT | 1
        assert hle_qemu_mock.regs.pc == RX_POPULATE_QUEUE_ADDRESS | 1

    def test_ethernetif_input_reads_dev_ptr_from_qemu_memory_when_netif_ptr_is_None(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ethernetif_input (
        #   struct netif * netif
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b594ba7163978faa6f67288054079ac
        hle_qemu_mock.avatar.callables = AVATAR_CALLABLES
        hle_qemu_mock.regs.lr = QEMU_LR_INIT
        hle_qemu_mock.regs.r0 = NETIF_PTR_FOR_ZERO_MEMORY_READ
        hle_qemu_mock.regs.pc = QEMU_PC_INIT
        hle.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        hle.netif_ptr = None
        hle.dev_ptr = DEV_PTR_FOR_ZERO_MEMORY_READ
        hle.org_lr = ORG_LR_INIT
        continue_, ret_val = hle.ethernetif_input(hle_qemu_mock, None)
        assert not continue_
        assert ret_val is None
        hle.model.get_frame_info.assert_called_once_with(ID)
        assert hle.org_lr == QEMU_LR_INIT
        assert hle_qemu_mock.regs.r0 == DEV_PTR_FOR_ZERO_MEMORY_READ
        assert hle_qemu_mock.regs.lr == QEMU_PC_INIT | 1
        assert hle_qemu_mock.regs.pc == RX_POPULATE_QUEUE_ADDRESS | 1

    def test_ethernetif_input_receives_frame_and_does_not_write_it_to_qemu_memory_when_frame_type_not_supported(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ethernetif_input (
        #   struct netif * netif
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b594ba7163978faa6f67288054079ac
        hle_qemu_mock.avatar.callables = AVATAR_CALLABLES
        hle_qemu_mock.regs.lr = QEMU_LR_INIT
        hle_qemu_mock.regs.r0 = NETIF_PTR
        hle_qemu_mock.regs.pc = QEMU_PC_INIT
        hle_qemu_mock.write_memory = mock.Mock()
        hle.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        hle.model.get_rx_frame = mock.Mock(return_value=(FRAME_DATA, 0))
        hle.netif_ptr = None
        hle.dev_ptr = None
        hle.org_lr = ORG_LR_INIT
        continue_, ret_val = hle.ethernetif_input(hle_qemu_mock, None)
        assert continue_
        assert ret_val is None
        hle.model.get_frame_info.assert_called_once_with(ID)
        hle.model.get_rx_frame.assert_called_once_with(ID, True)
        hle_qemu_mock.write_memory.assert_not_called()

    @pytest.mark.parametrize(
        "frame_data", [FRAME_DATA_0X0806, FRAME_DATA_0X0800]
    )
    def test_ethernetif_input_receives_frame_and_writes_it_to_qemu_memory_when_frame_type_supported(
        self, hle, hle_qemu_mock, frame_data
    ):
        # Associated HAL fuction declaration
        # void
        # ethernetif_input (
        #   struct netif * netif
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b594ba7163978faa6f67288054079ac
        hle_qemu_mock.avatar.callables = AVATAR_CALLABLES
        hle_qemu_mock.regs.lr = QEMU_LR_INIT
        hle_qemu_mock.regs.r0 = NETIF_PTR
        hle_qemu_mock.regs.pc = QEMU_PC_INIT
        hle_qemu_mock.write_memory = mock.Mock()
        hle.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, len(frame_data))
        )
        hle.model.get_rx_frame = mock.Mock(return_value=(frame_data, 0))
        hle.netif_ptr = None
        hle.dev_ptr = None
        hle.org_lr = ORG_LR_INIT
        continue_, ret_val = hle.ethernetif_input(hle_qemu_mock, None)
        assert not continue_
        assert ret_val is None
        hle.model.get_frame_info.assert_called_once_with(ID)
        hle.model.get_rx_frame.assert_called_once_with(ID, True)
        hle_qemu_mock.write_memory.assert_any_call(
            DEV_PTR + DEVICE_RX_PBUF, 4, 0, 1
        )
        hle_qemu_mock.write_memory.assert_any_call(
            PAYLOAD_PTR + PADDING, 1, frame_data, len(frame_data), raw=True
        )
        hle_qemu_mock.write_memory.assert_any_call(
            RX_BUF_PTR + PBUF_TOT_LEN, 2, len(frame_data), 1
        )
        hle_qemu_mock.write_memory.assert_any_call(
            RX_BUF_PTR + PBUF_LEN, 2, len(frame_data), 1
        )
        assert hle_qemu_mock.regs.r0 == RX_BUF_PTR
        assert hle_qemu_mock.regs.r1 == NETIF_PTR
        assert hle_qemu_mock.regs.pc == INPUT_FN_PTR
        assert hle.netif_ptr is None
        assert hle.dev_ptr is None

    @pytest.mark.xfail
    def test_low_level_output_transmits_empty_frame_when_pointer_to_data_is_zero(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # static err_t
        # ksz8851snl_low_level_output (
        #   struct netif * netif,
        #   struct pbuf * p
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b9fe1b1a5ac0c342bf656142b5a1075
        hle_qemu_mock.regs.r1 = ZERO_POINTER
        hle.model.tx_frame = mock.Mock()
        continue_, ret_val = hle.low_level_output(hle_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        hle.model.tx_frame.assert_called_once_with(ID, b"")

    @pytest.mark.xfail
    def test_low_level_output_transmits_frame_correctly_when_correct_pointer_to_qemu_memory_provided(
        self, hle, hle_qemu_mock
    ):
        # Associated HAL fuction declaration
        # static err_t
        # ksz8851snl_low_level_output (
        #   struct netif * netif,
        #   struct pbuf * p
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/sam0__spi__ksz8851snl_8c.html#a6b9fe1b1a5ac0c342bf656142b5a1075
        hle_qemu_mock.regs.r1 = BUF_TO_SEND_PTR
        hle.model.tx_frame = mock.Mock()
        continue_, ret_val = hle.low_level_output(hle_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        hle.model.tx_frame.assert_called_once_with(
            ID, FRAME_PART1_DATA_TO_SEND + FRAME_PART2_DATA_TO_SEND
        )

    def test_return_ok_just_returns_zero(self, hle):
        # Associated HAL fuction declaration
        # uint32_t
        # ksz8851snl_init (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga2a9144d219bbd2f325d338ee66560a02
        continue_, ret_val = hle.return_ok(None, None)
        assert continue_
        assert ret_val == 0
