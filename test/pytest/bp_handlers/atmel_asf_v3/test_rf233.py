from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.rf233 import RF233Radio

LR_RESET_VALUE = 0xFFFFFFFF

ID = "SAMR21Radio"

RF233_REG_IRQ_STATUS = 0x0F
RF233_REG_TRX_STATE = 0x02
RF233_REG_TRX_STATUS = 0x01
IRQ_TRX_END = 1 << 3

FRAME_PTR = 0x1600
LONG_FRAME_PTR = 0x1800
FRAME_LEN = 0x20
LONG_FRAME_LEN = 0xFFFFFF80
TRUNCATED_FRAME_LEN = 0x80
FRAME_DATA = b"\0x41" * FRAME_LEN
EXPECTED_FRAME_DATA = b"\0x45" * TRUNCATED_FRAME_LEN
BUFFER_PTR = 0x1800
BUFFER_LEN = 0x10
BIG_BUFFER_LEN = 0x100
IEEEAddr_ADDRESS = 0x2100
IEEEAddr_LEN = 8
IEEEAddr_VALUE = b"\0x20\0x30\0x40\0x50"

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    FRAME_PTR: [FRAME_DATA, 1, FRAME_LEN, True],
    LONG_FRAME_PTR: [EXPECTED_FRAME_DATA, 1, TRUNCATED_FRAME_LEN, True],
    IEEEAddr_ADDRESS: [IEEEAddr_VALUE, 1, IEEEAddr_LEN, True],
}


@pytest.fixture
def rf_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    qemu_mock.regs.lr = LR_RESET_VALUE
    return qemu_mock


@pytest.fixture
def radio():
    mock_model = mock.Mock()
    rf233 = RF233Radio(mock_model)
    # Define initial registers' values. The values do not have any specific meaning.
    # We just need to check that corresponding functions read them correctly.
    rf233.regs = {
        0x00: 100,
        RF233_REG_TRX_STATUS: 200,
        RF233_REG_TRX_STATE: 2200,
        0x03: 300,
        0x04: 400,
        0x05: 500,
        0x06: 600,
        0x07: 700,
        0x08: 800,
        0x09: 900,
        0x0A: 4400,
        0x0B: 5600,
        0x0C: 7800,
        0x0D: 9800,
        0x0E: 12300,
        # Special case. As the function under test does not return the real value of
        # the register the value should be 0 or IRQ_TRX_END
        RF233_REG_IRQ_STATUS: 77800,
    }
    return rf233


class TestRF233Radio:
    def test_send_reads_frame_from_qemu_memory_and_sends_it(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuctions declaration
        # int
        # rf233_send (
        #   const void * payload,
        #   unsigned short payload_len
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#a14cb425443bfc558b8255c71c024c1f1
        # void
        # trx_frame_write (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga9c44d5db802d54e24e3b1eea2b5df04a
        set_arguments(rf_qemu_mock, [FRAME_PTR, FRAME_LEN])
        radio.model.tx_frame = mock.Mock()
        continue_, ret_val = radio.send(rf_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        radio.model.tx_frame.assert_called_once_with(ID, FRAME_DATA)

    def test_send_treats_length_as_unsigned_char(self, radio, rf_qemu_mock):
        # Associated HAL fuctions declaration
        # int
        # rf233_send (
        #   const void * payload,
        #   unsigned short payload_len
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#a14cb425443bfc558b8255c71c024c1f1
        # void
        # trx_frame_write (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga9c44d5db802d54e24e3b1eea2b5df04a
        set_arguments(rf_qemu_mock, [LONG_FRAME_PTR, LONG_FRAME_LEN])
        radio.model.tx_frame = mock.Mock()
        continue_, ret_val = radio.send(rf_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        radio.model.tx_frame.assert_called_once_with(ID, EXPECTED_FRAME_DATA)

    def test_read_len_reports_zero_size_when_no_frames_available(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_read (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga0870e54a8d8ee347aeb1929f6d6a57f3
        # Note that the behavior currently modeled for this function doesn't correspond to the actual HAL function behavior -
        # the current implementation is only reporting the length of the frame, not the actual frame data.
        set_arguments(rf_qemu_mock, [FRAME_PTR])
        radio.model.has_frame = mock.Mock(return_value=None)
        radio.model.get_frame_info = mock.Mock()
        rf_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.read_len(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_frame_info.assert_not_called()
        rf_qemu_mock.write_memory.assert_called_once_with(FRAME_PTR, 1, 0, 1)

    def test_read_len_reports_first_frame_size_when_frames_available(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_read (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga0870e54a8d8ee347aeb1929f6d6a57f3
        NUMBER_OF_FRAMES = 3
        FRAME_LEN = 16
        FRAME_LEN_TO_QEMU = 18
        assert FRAME_LEN_TO_QEMU == FRAME_LEN + 2
        set_arguments(rf_qemu_mock, [FRAME_PTR])
        radio.model.has_frame = mock.Mock(return_value=True)
        radio.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, FRAME_LEN)
        )
        rf_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.read_len(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_frame_info.assert_called_once()
        rf_qemu_mock.write_memory.assert_called_once_with(
            FRAME_PTR, 1, FRAME_LEN_TO_QEMU, 1
        )

    def test_sram_read_does_not_call_get_first_frame_when_no_frames_available(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_sram_read (
        #  uint8_t addr,
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#gac02204bf4713055fab7ce34d2d099544
        set_arguments(rf_qemu_mock, [BUFFER_PTR])
        radio.model.has_frame = mock.Mock(return_value=None)
        radio.model.get_first_frame = mock.Mock()
        rf_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.sram_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_first_frame.assert_not_called()
        rf_qemu_mock.write_memory.assert_not_called()

    def test_sram_read_does_not_write_frame_to_qemu_memory_when_frame_len_greater_than_buffer(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_sram_read (
        #  uint8_t addr,
        #   uint8_t * data,
        #   uint8_t lengthqemu.regs.pc
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#gac02204bf4713055fab7ce34d2d099544
        set_arguments(rf_qemu_mock, [0, BUFFER_PTR, BUFFER_LEN])
        radio.model.has_frame = mock.Mock(return_value=True)
        radio.model.get_first_frame = mock.Mock(return_value=FRAME_DATA)
        rf_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.sram_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_first_frame.assert_called_once()
        rf_qemu_mock.write_memory.assert_not_called()

    def test_sram_read_writes_frame_to_qemu_memory_when_buffer_has_enough_memory_to_store_frame(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_sram_read (
        #  uint8_t addr,
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#gac02204bf4713055fab7ce34d2d099544
        set_arguments(rf_qemu_mock, [0, BUFFER_PTR, BIG_BUFFER_LEN])
        radio.model.has_frame = mock.Mock(return_value=True)
        radio.model.get_first_frame = mock.Mock(return_value=FRAME_DATA)
        rf_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.sram_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_first_frame.assert_called_once()
        rf_qemu_mock.write_memory.assert_called_once_with(
            BUFFER_PTR, 1, FRAME_DATA, len(FRAME_DATA), raw=True
        )

    @pytest.mark.parametrize("isr", [None, False, True])
    def test_on_enables_rx_isr_enabled(self, radio, isr):
        # Associated HAL fuction declaration
        # int
        # rf233_on (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#adbf7579c62974c99ac059f92f3a6a821
        radio.model.rx_isr_enabled = isr
        continue_, ret_val = radio.on(None, None)
        assert continue_
        assert ret_val == 0
        assert radio.model.rx_isr_enabled

    def test_get_channel_just_returns_zero(self, radio):
        # Associated HAL fuction declaration
        # int
        # rf_get_channel (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#a8adea0ffa23b6ff1ca6365c68d6914c3
        continue_, ret_val = radio.get_channel(None, None)
        assert continue_
        assert ret_val == 0

    def test_set_channel_just_returns_zero(self, radio):
        # Associated HAL fuction declaration
        # int
        # rf_set_channel (
        #   uint8_t ch
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#a91e63b2bbae8d6decb7ab765b43f33d5
        continue_, ret_val = radio.set_channel(None, None)
        assert continue_
        assert ret_val == 0

    def test_SetIEEEAddr_reads_IEEEAddr_from_qemu_memory_and_sets_it_correctly(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # SetIEEEAddr (
        #   uint8_t * ieee_addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/rf233_8c.html#ac34abc34d89edcab2d381144fad66d4a
        IEEEAddr_INIT_VALUE = b"\0xAA"
        set_arguments(rf_qemu_mock, [IEEEAddr_ADDRESS])
        radio.model.IEEEAddr = IEEEAddr_INIT_VALUE
        continue_, ret_val = radio.SetIEEEAddr(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert radio.model.IEEEAddr == IEEEAddr_VALUE

    def test_trx_reg_read_returns_zero_when_reg_is_RF233_REG_IRQ_STATUS_and_no_frame(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        set_arguments(rf_qemu_mock, [RF233_REG_IRQ_STATUS])
        radio.model.has_frame = mock.Mock(return_value=False)
        continue_, ret_val = radio.trx_reg_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_trx_reg_read_returns_IRQ_TRX_END_when_reg_is_RF233_REG_IRQ_STATUS_and_frame_exists(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        set_arguments(rf_qemu_mock, [RF233_REG_IRQ_STATUS])
        radio.model.has_frame = mock.Mock(return_value=True)
        continue_, ret_val = radio.trx_reg_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val == IRQ_TRX_END

    def test_trx_reg_read_returns_value_of_RF233_REG_TRX_STATE_when_reg_is_RF233_REG_TRX_STATUS(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        set_arguments(rf_qemu_mock, [RF233_REG_TRX_STATUS])
        continue_, ret_val = radio.trx_reg_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val == radio.regs[RF233_REG_TRX_STATE]

    @pytest.mark.parametrize(
        "reg", set(range(16)) - {RF233_REG_IRQ_STATUS, RF233_REG_TRX_STATUS}
    )
    def test_trx_reg_read_returns_value_of_reg_correctly(
        self, radio, rf_qemu_mock, reg
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        set_arguments(rf_qemu_mock, [reg])
        continue_, ret_val = radio.trx_reg_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val == radio.regs[reg]

    @pytest.mark.parametrize("reg", set(range(16, 256)))
    def test_trx_reg_read_returns_zero_for_non_existing_reg(
        self, radio, rf_qemu_mock, reg
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        set_arguments(rf_qemu_mock, [reg])
        continue_, ret_val = radio.trx_reg_read(rf_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_trx_spi_init_set_qemu_registers_correctly(
        self, radio, rf_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_spi_init (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#ga8a90c8aa5a372bbacac077e99ed69233
        PC_VALUE = 0xAAAA
        INIT_REG = [10, 20, 30, 40]
        AT86RFX_ISR_ADDRESS = 0x24800
        AT86RFX_ISR_ADDRESS_EXPECTED = 0x24801
        assert AT86RFX_ISR_ADDRESS_EXPECTED == AT86RFX_ISR_ADDRESS | 1
        EXTINT_REGISTER_CALLBACK_ADDRESS = 0x38A00
        EXTINT_REGISTER_CALLBACK_ADDRESS_EXPECTED = 0x38A01
        assert (
            EXTINT_REGISTER_CALLBACK_ADDRESS_EXPECTED
            == EXTINT_REGISTER_CALLBACK_ADDRESS | 1
        )
        AVATAR_CALLABLES = {
            "AT86RFX_ISR": AT86RFX_ISR_ADDRESS,
            "extint_register_callback": EXTINT_REGISTER_CALLBACK_ADDRESS,
        }
        set_arguments(rf_qemu_mock, INIT_REG)
        rf_qemu_mock.regs.pc = PC_VALUE
        rf_qemu_mock.avatar.callables = AVATAR_CALLABLES
        continue_, ret_val = radio.trx_spi_init(rf_qemu_mock, None)
        assert not continue_
        assert ret_val is None
        assert rf_qemu_mock.regs.r0 == AT86RFX_ISR_ADDRESS_EXPECTED
        assert rf_qemu_mock.regs.r1 == 0
        assert rf_qemu_mock.regs.r2 == 0
        assert (
            rf_qemu_mock.regs.pc == EXTINT_REGISTER_CALLBACK_ADDRESS_EXPECTED
        )

    @pytest.mark.parametrize("reg", set(range(16)))
    @pytest.mark.parametrize("value", [11, 222, 3333])
    def test_trx_reg_write_writes_value_to_regs_correctly(
        self, radio, rf_qemu_mock, reg, value
    ):
        # Associated HAL fuction declaration
        # void
        # trx_reg_write (
        #   uint8_t addr,
        #   uint8_t data
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/group__group__trx__access.html#gaf62c122391b239c175cbd1829f543a1f
        set_arguments(rf_qemu_mock, [reg, value])
        continue_, ret_val = radio.trx_reg_write(rf_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert radio.regs[reg] == value
