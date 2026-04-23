from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.ethernet_ksz8851 import Ksz8851Eth

LR_RESET_VALUE = 0xFFFFFFFF

ID = "ksz8851"

# SPI Addresses
REG_RX_FHR_BYTE_CNT = 0x7E
REG_RX_FHR_STATUS = 0x7C
REG_TX_MEM_INFO = 0x78
REG_INT_MASK = 0x90
REG_CHIP_ID = 0xC0
REG_PHY_STATUS = 0xE6
REG_RX_FRAME_CNT_THRES = 0x9C

RX_VALID_ETH = 0x8008  # Frame is valid and Eth Frame for REG_RX_FHR_STATUS
PHY_STATUS_UP_100TX_FD = 0x4004

REG_CHIP_ID = 0xC0
CHIP_ID = 0x8870

PADDING = 2
CRC_SIZE = 4

ZERO_FRAMES = ZERO_FRAME_SIZE = 0
NUMBER_OF_FRAMES = 2
SIZE_1ST_FRAME = 16
FRAME_DATA = b"\x25" * SIZE_1ST_FRAME

BUFFER_ADDRESS = 0x1500
BUFFER_LEN = 128
BUFFER_LEN_SHORT = 8

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    BUFFER_ADDRESS: [FRAME_DATA, 1, SIZE_1ST_FRAME, True],
}


@pytest.fixture
def ksz_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    qemu_mock.regs.lr = LR_RESET_VALUE
    return qemu_mock


@pytest.fixture
def ksz():
    mock_model = mock.Mock()
    ksz8851 = Ksz8851Eth(mock_model)
    # setting registers data
    for i in range(256):
        ksz8851.regs[i] = 0x100 + i
    return ksz8851


class TestEthernetSmartConnect:
    # Associated HAL fuction declaration for all test_read_reg_... functions
    # uint16_t
    # ksz8851_reg_read (
    #   uint16_t reg
    # )
    # The under test function's description can be found here -
    # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#gab2cc5ac8804e8b88e4dd6dad31ffeb45
    def test_read_reg_returns_frame_length_plus_crc_size_for_REG_RX_FHR_BYTE_CNT(
        self, ksz, ksz_qemu_mock
    ):
        ksz_qemu_mock.regs.r0 = REG_RX_FHR_BYTE_CNT
        ksz.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == SIZE_1ST_FRAME + CRC_SIZE
        ksz.model.get_frame_info.assert_called_once_with(ID)

    def test_read_reg_returns_zero_for_REG_RX_FHR_STATUS_when_no_frames(
        self, ksz, ksz_qemu_mock
    ):
        ksz_qemu_mock.regs.r0 = REG_RX_FHR_STATUS
        ksz.model.get_frame_info = mock.Mock(
            return_value=(ZERO_FRAMES, ZERO_FRAME_SIZE)
        )
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        ksz.model.get_frame_info.assert_called_once_with(ID)

    def test_read_reg_returns_RX_VALID_ETH_for_REG_RX_FHR_STATUS_when_frames_present(
        self, ksz, ksz_qemu_mock
    ):
        ksz_qemu_mock.regs.r0 = REG_RX_FHR_STATUS
        ksz.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == RX_VALID_ETH
        ksz.model.get_frame_info.assert_called_once_with(ID)

    @pytest.mark.parametrize("number_of_frames", set(range(0x100, 0x200)))
    def test_read_reg_returns_thres_value_for_REG_RX_FRAME_CNT_THRES_when_number_of_frames_more_than_255(
        self, ksz, ksz_qemu_mock, number_of_frames
    ):
        THRES_VALUE = 0xFF << 8 & 0xFFFF
        ksz_qemu_mock.regs.r0 = REG_RX_FRAME_CNT_THRES
        ksz.model.get_frame_info = mock.Mock(
            return_value=(number_of_frames, SIZE_1ST_FRAME)
        )
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == THRES_VALUE
        ksz.model.get_frame_info.assert_called_once_with(ID)

    @pytest.mark.parametrize(
        "number_of_frames, thres_value",
        [(i, i << 8 & 0xFFFF) for i in range(256)],
    )
    def test_read_reg_returns_correct_thres_value_for_REG_RX_FRAME_CNT_THRES_when_number_of_frames_not_more_than_255(
        self, ksz, ksz_qemu_mock, number_of_frames, thres_value
    ):
        ksz_qemu_mock.regs.r0 = REG_RX_FRAME_CNT_THRES
        ksz.model.get_frame_info = mock.Mock(
            return_value=(number_of_frames, SIZE_1ST_FRAME)
        )
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == thres_value
        ksz.model.get_frame_info.assert_called_once_with(ID)

    def test_read_reg_returns_PHY_STATUS_UP_100TX_FD_for_REG_PHY_STATUS(
        self, ksz, ksz_qemu_mock
    ):
        ksz_qemu_mock.regs.r0 = REG_PHY_STATUS
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == PHY_STATUS_UP_100TX_FD

    def test_read_reg_returns_CHIP_ID_for_REG_CHIP_ID(
        self, ksz, ksz_qemu_mock
    ):
        ksz_qemu_mock.regs.r0 = REG_CHIP_ID
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == CHIP_ID

    def test_read_reg_returns_max_value_for_REG_TX_MEM_INFO(
        self, ksz, ksz_qemu_mock
    ):
        MAX_VALUE = 0x1FFF  # hard-coded in implemtation code
        ksz_qemu_mock.regs.r0 = REG_TX_MEM_INFO
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == MAX_VALUE

    @pytest.mark.parametrize(
        "reg",
        set(range(256))
        - {
            REG_RX_FHR_BYTE_CNT,
            REG_RX_FHR_STATUS,
            REG_RX_FHR_STATUS,
            REG_RX_FRAME_CNT_THRES,
            REG_RX_FRAME_CNT_THRES,
            REG_PHY_STATUS,
            REG_CHIP_ID,
            REG_TX_MEM_INFO,
        },
    )
    def test_read_reg_returns_register_value_for_non_special_register(
        self, ksz, ksz_qemu_mock, reg
    ):
        ksz_qemu_mock.regs.r0 = reg
        continue_, ret_val = ksz.read_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val == ksz.regs[reg]

    # Associated HAL fuction declaration for all test_write_reg_... functions
    # void
    # ksz8851_reg_write	(
    #   int16_t reg,
    #   uint16_t wrdata
    # )
    # The under test function's description can be found here -
    # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga01ce750a065491a8b3d00bc7c1b3454b
    @pytest.mark.parametrize(
        "reg,value",
        [(i, i + 0x300) for i in list(set(range(256)) - {REG_INT_MASK})],
    )
    def test_write_reg_writes_value_to_register_correctly_for_REG_INT_MASK_register(
        self, ksz, ksz_qemu_mock, reg, value
    ):
        ksz_qemu_mock.regs.r0 = reg
        ksz_qemu_mock.regs.r1 = value
        continue_, ret_val = ksz.write_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert ksz.regs[reg] == value

    @pytest.mark.parametrize(
        "init_value", [0x00, 0x01, 0x10, 0x0100, 0x4000, 0x1FFF]
    )
    def test_write_reg_disables_rx_isr_when_register_is_REG_INT_MASK_and_rx_isr_bit_set_to_off(
        self, ksz, ksz_qemu_mock, init_value
    ):
        ksz_qemu_mock.regs.r0 = REG_INT_MASK
        ksz_qemu_mock.regs.r1 = init_value
        ksz.model.disable_rx_isr = mock.Mock()
        continue_, ret_val = ksz.write_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        ksz.model.disable_rx_isr.assert_called_once_with(ID)

    def test_write_reg_enables_rx_isr_when_register_is_REG_INT_MASK_and_rx_isr_bit_set_to_on(
        self, ksz, ksz_qemu_mock
    ):
        REG_INT_MASK_VALUE = 0x2000  # rx isr bit is 0x2000
        ksz_qemu_mock.regs.r0 = REG_INT_MASK
        ksz_qemu_mock.regs.r1 = REG_INT_MASK_VALUE
        ksz.model.enable_rx_isr = mock.Mock()
        continue_, ret_val = ksz.write_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        ksz.model.enable_rx_isr.assert_called_once_with(ID)

    @pytest.mark.parametrize("reg", range(256))
    @pytest.mark.parametrize(
        "value,res", [(1 << i, 0xFFFF - (1 << i)) for i in range(16)]
    )
    def test_clr_reg_clears_bit_of_register_correctly(
        self, ksz, ksz_qemu_mock, reg, value, res
    ):
        # Associated HAL fuction declaration
        # void
        # ksz8851_reg_clrbits (
        #   int16_t reg,
        #   uint16_t bits_to_clr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#gaf86c2316204247802c176b42cfd8a8db
        REG_INT_VALUE = 0xFFFF
        ksz.regs[reg] = REG_INT_VALUE
        ksz_qemu_mock.regs.r0 = reg
        ksz_qemu_mock.regs.r1 = value
        continue_, ret_val = ksz.clr_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert ksz.regs[reg] == res

    @pytest.mark.parametrize("reg", range(256))
    @pytest.mark.parametrize("init_value", [0x00, 0x0F, 0xF0, 0x0F00, 0xF000])
    @pytest.mark.parametrize("value", [(i) for i in range(16)])
    def test_set_reg_sets_bit_of_register_correctly(
        self, ksz, ksz_qemu_mock, reg, init_value, value
    ):
        # Associated HAL fuction declaration
        # void
        # ksz8851_reg_setbits (
        #   int16_t reg,
        #   uint16_t bits_to_set
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga2f21e451bc9851a795b7fb4fcc567eb1
        ksz.regs[reg] = init_value
        ksz_qemu_mock.regs.r0 = reg
        ksz_qemu_mock.regs.r1 = value
        continue_, ret_val = ksz.set_reg(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert ksz.regs[reg] == init_value | value

    @pytest.mark.parametrize("init_frame", [None, [b"Hello", b"Bye"]])
    def test_fifo_write_begin_clears_frame_list(self, ksz, init_frame):
        # Associated HAL fuction declaration
        # void
        # ksz8851_fifo_write_begin (
        #   uint32_t tot_len
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga2eea658c6f2ac30b5b7ada24aea62079
        ksz.frame = init_frame
        continue_, ret_val = ksz.fifo_write_begin(None, None)
        assert continue_
        assert ret_val is None
        assert ksz.frame == []

    def test_fifo_write_reads_data_from_qemu_memory_and_appends_it_to_frame_list(
        self, ksz, ksz_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ksz8851_fifo_write (
        #   uint8_t * buf,
        #   uint32_t len
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga1ce32f455ee61cf9d66638681028596e
        INIT_VALUE = b"Hello"
        set_arguments(ksz_qemu_mock, [BUFFER_ADDRESS, SIZE_1ST_FRAME])
        ksz.frame = [INIT_VALUE]
        continue_, ret_val = ksz.fifo_write(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert ksz.frame == [INIT_VALUE, FRAME_DATA]

    @pytest.mark.xfail
    def test_fifo_write_end_sends_frame_correctly(self, ksz):
        # Associated HAL fuction declaration
        # void
        # ksz8851_fifo_write_end (
        #   uint32_t pad
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#gaee1d426995ec6de75eca21428f94fd50
        INIT_FRAME = [b"Hello", b"Bye"]
        JOINED_FRAME = b"".join(INIT_FRAME)
        ksz.frame = INIT_FRAME
        ksz.model.tx_frame = mock.Mock()
        continue_, ret_val = ksz.fifo_write_end(None, None)
        assert continue_
        assert ret_val is None
        ksz.model.tx_frame.assert_called_once_with(ID, JOINED_FRAME)

    def test_fifo_read_receives_frame_and_write_it_to_qemu_memory_correctly(
        self, ksz, ksz_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # ksz8851_fifo_read (
        #   uint8_t * buf,
        #   uint32_t len
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#gad77aac38949c6b79b4dd22ca80875a1e
        set_arguments(ksz_qemu_mock, [BUFFER_ADDRESS, SIZE_1ST_FRAME])
        ksz_qemu_mock.write_memory = mock.Mock()
        ksz.model.get_rx_frame = mock.Mock(
            side_effect=[(None, 0), (None, 0), (FRAME_DATA, 0)]
        )
        continue_, ret_val = ksz.fifo_read(ksz_qemu_mock, None)
        assert continue_
        assert ret_val is None
        ksz.model.get_rx_frame.assert_called_with(ID, True)
        ksz_qemu_mock.write_memory.assert_called_once_with(
            BUFFER_ADDRESS + PADDING, 1, FRAME_DATA, SIZE_1ST_FRAME, raw=True
        )

    def test_ksz_return_ok_just_returns_zero(self, ksz):
        # Associated HAL fuction declaration
        # uint32_t
        # ksz8851snl_init (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/saml21/html/group__ksz8851snl__phy__controller__group.html#ga2a9144d219bbd2f325d338ee66560a02
        continue_, ret_val = ksz.ksz_return_ok(None, None)
        assert continue_
        assert ret_val == 0

    def test_ksz_return_void_just_returns_None(self, ksz):
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
        continue_, ret_val = ksz.ksz_return_void(None, None)
        assert continue_
        assert ret_val is None
