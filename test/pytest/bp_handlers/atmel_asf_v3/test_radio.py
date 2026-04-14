from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.radio import SAMR21Radio

LR_RESET_VALUE = 0xFFFFFFFF

RF233_REG = 0x02
PRESET_VALUE = 11
NEW_VALUE = 0xEE

DATA_PTR = 0x1600
DATA_LEN = 0x20
SHORT_DATA_LEN = 0x05
BUFFER_DATA = b"\0x41" * DATA_LEN
FRAME_LEN = 0x10
FRAME_DATA = b"\0x20" * FRAME_LEN
RX_TIME = 15000

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    DATA_PTR: [BUFFER_DATA, 1, DATA_LEN, True],
}


@pytest.fixture
def sd_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    qemu_mock.regs.lr = LR_RESET_VALUE
    return qemu_mock


@pytest.fixture
def radio():
    mock_model = mock.Mock()
    return SAMR21Radio(mock_model)


class TestSAMR21Radio:
    @pytest.mark.parametrize("reg", [1, 2, 3, 5, 8])
    @pytest.mark.parametrize("reg_value", [1, 2, 3, 6])
    def test_read_reg_returns_value_of_provided_register(
        self, radio, sd_qemu_mock, reg, reg_value
    ):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_reg_read (
        #   uint8_t addr
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga414c729c9a46fe23bfae05c8055dab5d
        radio.regs[reg] = reg_value
        set_arguments(sd_qemu_mock, [reg])
        continue_, ret_val = radio.read_reg(sd_qemu_mock, None)
        assert continue_
        assert ret_val == reg_value

    @pytest.mark.parametrize("reg", [0, 1] + list(range(3, 16)))
    def test_write_reg_does_not_write_to_non_RF233_register(
        self, radio, sd_qemu_mock, reg
    ):
        # Associated HAL fuction declaration
        # void
        # trx_reg_write (
        #   uint8_t addr,
        #   uint8_t data
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#gaf62c122391b239c175cbd1829f543a1f
        radio.regs[reg] = PRESET_VALUE
        set_arguments(sd_qemu_mock, [reg, NEW_VALUE])
        continue_, ret_val = radio.write_reg(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert radio.regs[reg] == PRESET_VALUE

    def test_write_reg_writes_to_RF233_register(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # trx_reg_write (
        #   uint8_t addr,
        #   uint8_t data
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#gaf62c122391b239c175cbd1829f543a1f
        radio.regs[RF233_REG] = PRESET_VALUE
        set_arguments(sd_qemu_mock, [RF233_REG, NEW_VALUE])
        continue_, ret_val = radio.write_reg(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        assert radio.regs[RF233_REG] == NEW_VALUE

    def test_read_bit_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # uint8_t
        # trx_bit_read (
        #   uint8_t addr,
        #   uint8_t mask,
        #   uint8_t pos
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga8181ee8ff168425d233bed1105811df9
        # The read_bit function ignores the argument passed in for actual behavior, but does retrieve r0 for purposes of logging.
        # Pass dummy value for the argument. Will need to set actual proper value if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 5
        continue_, ret_val = radio.read_bit(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None

    def test_write_bit_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # trx_bit_write (
        #   uint8_t addr,
        #   uint8_t mask,
        #   uint8_t pos,
        #   uint8_t new_value
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga0346c74d62bc3b803636a6cd960d5f7e
        # The write_bit function ignores the argument passed in for actual behavior, but does retrieve r0 for purposes of logging.
        # Pass dummy value for the argument. Will need to set actual proper value if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 5
        continue_, ret_val = radio.write_bit(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None

    def test_read_frame_does_not_read_frame_and_does_not_write_to_qemu_memory_when_no_frame(
        self, radio, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_read (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga0870e54a8d8ee347aeb1929f6d6a57f3
        set_arguments(sd_qemu_mock, [DATA_PTR, DATA_LEN])
        radio.model.has_frame = mock.Mock(return_value=False)
        radio.model.get_first_frame_and_time = mock.Mock(
            return_value=(FRAME_DATA, RX_TIME)
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.read_frame(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.get_first_frame_and_time.assert_not_called()
        sd_qemu_mock.write_memory.assert_not_called()

    @pytest.mark.xfail
    def test_read_frame_reads_frame_and_does_not_write_to_qemu_memory_when_destination_buffer_too_small(
        self, radio, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_read (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga0870e54a8d8ee347aeb1929f6d6a57f3
        set_arguments(sd_qemu_mock, [DATA_PTR, SHORT_DATA_LEN])
        radio.model.has_frame = mock.Mock(return_value=True)
        radio.model.get_first_frame_and_time = mock.Mock(
            return_value=(FRAME_DATA, RX_TIME)
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.read_frame(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.has_frame.assert_called_once_with()
        radio.model.get_first_frame_and_time.assert_called_once_with()
        sd_qemu_mock.write_memory.assert_not_called()

    @pytest.mark.xfail
    def test_read_frame_reads_frame_and_writes_to_qemu_memory_when_enough_space(
        self, radio, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_read (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga0870e54a8d8ee347aeb1929f6d6a57f3
        set_arguments(sd_qemu_mock, [DATA_PTR, DATA_LEN])
        radio.model.has_frame = mock.Mock(return_value=True)
        radio.model.get_first_frame_and_time = mock.Mock(
            return_value=(FRAME_DATA, RX_TIME)
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = radio.read_frame(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.has_frame.assert_called_once_with()
        radio.model.get_first_frame_and_time.assert_called_once_with()
        sd_qemu_mock.write_memory.assert_called_once_with(
            DATA_PTR, 1, FRAME_DATA, FRAME_LEN
        )

    def test_write_frame_reads_data_from_qemu_memory_and_writes_frame(
        self, radio, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # void
        # trx_frame_write (
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga9c44d5db802d54e24e3b1eea2b5df04a
        ID = "SAMR21Radio"
        set_arguments(sd_qemu_mock, [DATA_PTR, DATA_LEN])
        radio.model.tx_frame = mock.Mock()
        continue_, ret_val = radio.write_frame(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
        radio.model.tx_frame.assert_called_once_with(ID, BUFFER_DATA)

    def test_sram_read_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # trx_sram_read (
        #   uint8_t addr,
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#gac02204bf4713055fab7ce34d2d099544
        # The sram_read function ignores the arguments passed in for actual behavior, but does retrieve r0 and r1 for purposes of logging.
        # Pass dummy values for those arguments. Will need to set actual proper values if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 1
        sd_qemu_mock.regs.r1 = 2
        continue_, ret_val = radio.sram_read(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None

    def test_sram_write_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # trx_sram_write (
        #   uint8_t addr,
        #   uint8_t * data,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#gaf2ca9e5088b45986a409b29194d22a2d
        # The sram_write function ignores the arguments passed in for actual behavior, but does retrieve r0 and r1 for purposes of logging.
        # Pass dummy values for those arguments. Will need to set actual proper values if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 1
        sd_qemu_mock.regs.r1 = 2
        continue_, ret_val = radio.sram_write(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None

    def test_aes_wrrd_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # trx_aes_wrrd (
        #   uint8_t addr,
        #   uint8_t * ldata,
        #   uint8_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga0372653e564e05a15661f407391c54b2
        # The aes_wrrd function ignores the arguments passed in for actual behavior, but does retrieve r0 and r1 for purposes of logging.
        # Pass dummy values for those arguments. Will need to set actual proper values if the implementation function's fidelity is improved.
        sd_qemu_mock.regs.r0 = 1
        sd_qemu_mock.regs.r1 = 2
        continue_, ret_val = radio.aes_wrrd(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None

    def test_nop_return_void_just_returns_None(self, radio, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # PhyReset (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/thirdparty.wireless.avr2025_mac.apps.tal.performance_analyzer.saml21_xplained_pro_b_rf212b/html/group__group__trx__access.html#ga2d6e87e700147cce2f59cf8ed98af525
        continue_, ret_val = radio.nop_return_void(sd_qemu_mock, None)
        assert continue_
        assert ret_val is None
