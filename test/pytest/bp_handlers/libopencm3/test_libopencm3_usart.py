from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.libopencm3.libopencm3_usart import (
    LIBOPENCM3_USART,
)

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
    return LIBOPENCM3_USART(mock_model)


class TestLIBOPENCM3_USART:
    def test_return_ok_just_returns_zero(self, uart):
        # Associated HAL functions declaration
        # void
        # usart_set_baudrate (
        #   uint32_t usart,
        #   uint32_t baud
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L49
        # void
        # usart_enable (
        #   uint32_t usart
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L180
        continue_, retval = uart.return_ok(None, None)
        assert continue_
        assert retval == 0

    def test_write_single_writes_second_argument_to_uart_correctly(
        self, uart, uart_qemu_mock
    ):
        # Associated HAL functions declaration
        # void
        # usart_send_blocking (
        #   uint32_t usart,
        #   uint16_t data
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_all.c#L210
        # void
        # usart_send (
        #   uint32_t usart,
        #   uint16_t data
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_f124.c#L44
        set_arguments(uart_qemu_mock, [USART_PTR, REG_DATA])
        uart.model.write = mock.Mock()
        continue_, ret_val = uart.write_single(uart_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        uart.model.write.assert_called_once_with(HW_ADDR, UART_DATA_FROM_REG)

    def test_read_single_reads_data_from_uart_and_returns_it_correctly(
        self, uart, uart_qemu_mock
    ):
        # Associated HAL function declaration
        # uint16_t
        # usart_recv (
        #   uint32_t usart
        # )
        # The under test function's description can be found here -
        # https://github.com/libopencm3/libopencm3/blob/4a378a729a9f9b7f24e527e74dd38b5ae3b9bc69/lib/stm32/common/usart_common_f124.c#L61
        DATA_FROM_UART = 0x0301
        DATA_FROM_UART_BYTES = b"\x03\x01"
        set_arguments(uart_qemu_mock, [USART_PTR])
        uart_qemu_mock.write_memory = mock.Mock()
        uart.model.read = mock.Mock(return_value=DATA_FROM_UART_BYTES)
        continue_, ret_val = uart.read_single(uart_qemu_mock, None)
        assert continue_
        assert ret_val == DATA_FROM_UART
        uart.model.read.assert_called_once_with(HW_ADDR, 1, block=True)
