from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_uart import STM32F4UART

# The first parameter to HAL_UART_* functions is a pointer to one of
# these [1]:
#
# typedef struct
# {
#   USART_TypeDef                 *Instance;
#   ...
# }UART_HandleTypeDef;
#
#
# The implementation code treats the 'Instance' member as a unique
# identifier for the device that should be used. That *looks* like it
# corresponds to some example code I found [2], so it seems reasonably
# likely to be correct.
#
# [1] https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/
#       STM32F439xx_User_Manual/stm32f4xx__hal__uart_8h_source.html
#     (lines 158-191)
#
# [2] https://simonmartin.ch/resources/stm32/dl/
#       STM32%20Tutorial%2003%20-%20UART%20Communication%20using%20HAL%20(and%20FreeRTOS).pdf
#
#
# These two constants are arbitrary values that are conceptually
# stored in one of these 'Instance' members.
USART1_ID = 0x1000
USART2_ID = 0x2000

# In a moment, we will have a fixture that sets up a virtual
# UART_HandleTypeDef instance somewhere in memory for use in the
# tests. Those instances will be located at the following addresses:
UART1_INSTANCE1_ADDRESS = 0x11000
UART1_INSTANCE2_ADDRESS = 0x11100
UART2_INSTANCE1_ADDRESS = 0x12000


# Constants used by the Transmit test, also needed when setting up the memory fake.
TRANSMIT_READ_ADDRESS = 0x8000
TRANSMIT_DATA = b"Howdy world"
TRANSMIT_SIZE = len(TRANSMIT_DATA)


# The difference between UART1_* and UART2_* allows us to ensure that
# the implementation code properly distinguishes between use of the
# two different UART devices. The difference between *_INSTANCE1_* and
# *_INSTANCE2_* is to make sure that it's possible to set up multiple
# objects that refer to the same device.
MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    UART1_INSTANCE1_ADDRESS: [USART1_ID],
    UART1_INSTANCE2_ADDRESS: [USART1_ID],
    UART2_INSTANCE1_ADDRESS: [USART2_ID],
    TRANSMIT_READ_ADDRESS: [TRANSMIT_DATA, 1, TRANSMIT_SIZE, True],
}


# Finally, we have a fixture that puts this stuff in memory, and sets
# up the weird read_memory() test double.
@pytest.fixture
def uart_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def uart():
    mock_model = mock.Mock()
    return STM32F4UART(mock_model)


class TestStm32f4Uart:
    def test_Init_just_returns_0(self, uart):
        continue_, retval = uart.hal_ok(None, None)
        assert continue_
        assert retval == 0

    def test_GetState_just_returns_0x20(self, uart):
        # HAL_UART_StateTypeDef HAL_UART_GetState(UART_HandleTypeDef *huart)
        #
        # enum HAL_UART_StateTypeDef {
        #    HAL_UART_STATE_RESET = 0x00U,
        #    HAL_UART_STATE_READY = 0x20U,
        #    ...,
        # }
        #
        # so the 0x20 return == HAL_UART_STATE_READY.
        continue_, retval = uart.get_state(None, None)
        assert continue_
        assert retval == 0x20

    def test_Receive_places_received_data_in_memory(
        self, uart, uart_qemu_mock
    ):
        # HAL_StatusTypeDef
        # HAL_UART_Receive (
        #     UART_HandleTypeDef *huart,
        #     uint8_t *pData,
        #     uint16_t Size,
        #     uint32_t Timeout
        # )
        #
        # 'Timeout' isn't honored, and indeed the *_IT and *_DMA
        # version of these functions leave out that parameter (but the
        # first three are the same).
        #
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/
        #    STM32F439xx_User_Manual/group__uart__exported__functions__group2.html

        STORE_ADDRESS = 0x8000  # arbitrary
        DATA = b"Hello World"
        RECEIVE_SIZE = len(DATA)

        #########
        # Arrange
        uart.model.read.return_value = DATA
        set_arguments(
            uart_qemu_mock,
            [UART1_INSTANCE1_ADDRESS, STORE_ADDRESS, RECEIVE_SIZE],
        )

        #####
        # Act
        continue_, retval = uart.handle_rx(uart_qemu_mock, None)

        ########
        # Assert
        assert continue_
        assert retval == 0  # this is a status, not rx size; see docs

        # Make sure that handle_rx() queried the peripheral model correctly:
        uart.model.read.assert_called_once_with(
            USART1_ID, RECEIVE_SIZE, block=True
        )

        # and that it then wrote the expected data into QEMU memory
        uart_qemu_mock.write_memory.assert_called_once_with(
            STORE_ADDRESS,  # address
            1,  # wordsize
            DATA,  # val
            RECEIVE_SIZE,  # num_words
            raw=True,
        )

    def test_Transmit_transfers_requested_data(self, uart, uart_qemu_mock):
        # HAL_StatusTypeDef
        # HAL_UART_Transmit (
        #     UART_HandleTypeDef *huart,
        #     uint8_t *pData,
        #     uint16_t Size,
        #     uint32_t Timeout
        # )
        #
        # 'Timeout' isn't honored, and indeed the *_IT and *_DMA
        # version of these functions leave out that parameter (but the
        # first three are the same).
        #
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/
        #    STM32F439xx_User_Manual/group__uart__exported__functions__group2.html
        set_arguments(
            uart_qemu_mock,
            [UART1_INSTANCE1_ADDRESS, TRANSMIT_READ_ADDRESS, TRANSMIT_SIZE],
        )

        continue_, retval = uart.handle_tx(uart_qemu_mock, None)

        assert continue_
        assert retval == 0  # this is a status, not rx size; see docs
        uart.model.write.assert_called_once_with(USART1_ID, TRANSMIT_DATA)
