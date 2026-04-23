from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.usart import USART

USART_PTR = 0x1000
HW_ADDR = 0x2400
BUFFER_ADDR = 0x1600
BUFFER = b"qwertyuiopasdfgh"
BUFFER_LEN = len(BUFFER)
REG_DATA = 0xDEAD
UART_DATA_FROM_REG = bytes.fromhex("DEAD")
DEST_ADDR = 0x2800


MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    USART_PTR: [HW_ADDR],
    BUFFER_ADDR: [BUFFER, 1, BUFFER_LEN, True],
}


@pytest.fixture
def uart_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def uart():
    mock_model = mock.Mock()
    return USART(mock_model)


class TestUSART:
    def test_return_ok_just_returns_zero(self, uart):
        # Associated HAL fuctions declaration
        # enum status_code
        # usart_init (
        #   struct usart_module *const module,
        #   Sercom *const hw,
        #   const struct usart_config *const config
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.samba_bootloader.saml21_xplained_pro/html/group__asfdoc__sam0__sercom__usart__group.html#gad67046f395137b2a7a1ef72f83907674
        # static void
        # usart_enable (
        #   const struct usart_module *const module
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.samba_bootloader.saml21_xplained_pro/html/group__asfdoc__sam0__sercom__usart__group.html#gab7e61f9deb78cc66ff3b8738cb3de2f3
        continue_, retval = uart.return_ok(None, None)
        assert continue_
        assert retval == 0

    def test_write_buffer_reads_buffer_from_qemu_memory_and_writes_it_to_uart_correctly(
        self, uart, uart_qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # usart_write_buffer_wait (
        #   struct usart_module *const module,
        #   const uint8_t * tx_data,
        #   uint16_t length
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.samba_bootloader.saml21_xplained_pro/html/group__asfdoc__sam0__sercom__usart__group.html#gacffd0845249348d37d14c65a41132e41
        set_arguments(uart_qemu_mock, [USART_PTR, BUFFER_ADDR, BUFFER_LEN])
        uart.model.write = mock.Mock()
        continue_, ret_val = uart.write_buffer(uart_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        uart.model.write.assert_called_with(HW_ADDR, BUFFER)

    @pytest.mark.xfail
    def test_write_single_writes_second_argument_to_uart_correctly(
        self, uart, uart_qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # usart_write_wait (
        #   struct usart_module *const module,
        #   const uint16_t tx_data
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.samba_bootloader.saml21_xplained_pro/html/group__asfdoc__sam0__sercom__usart__group.html#gaee8b142e8ad13e1e226334a9954e853c
        set_arguments(uart_qemu_mock, [USART_PTR, REG_DATA])
        uart.model.write = mock.Mock()
        continue_, ret_val = uart.write_single(uart_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        uart.model.write.assert_called_once_with(HW_ADDR, UART_DATA_FROM_REG)

    @pytest.mark.xfail
    def test_read_single_reads_data_from_uart_and_writes_to_qemu_memory_correctly(
        self, uart, uart_qemu_mock
    ):
        # Associated HAL fuction declaration
        # enum status_code
        # usart_write_wait (
        #   struct usart_module *const module,
        #   uint16_t *const rx_data
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/sam0.applications.samba_bootloader.saml21_xplained_pro/html/group__asfdoc__sam0__sercom__usart__group.html#gaf7db90c51a6f17edff5f1de2a0e3d8a5
        DATA_FROM_UART = b"\x00\x20"
        WORD_SIZE = 2
        ONE_WORD = 1
        set_arguments(uart_qemu_mock, [USART_PTR, DEST_ADDR])
        uart_qemu_mock.write_memory = mock.Mock()
        uart.model.read = mock.Mock(return_value=DATA_FROM_UART)
        continue_, ret_val = uart.read_single(uart_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        uart.model.read.assert_called_once_with(HW_ADDR, 1, block=True)
        uart_qemu_mock.write_memory.assert_called_once_with(
            DEST_ADDR, WORD_SIZE, DATA_FROM_UART, ONE_WORD
        )
