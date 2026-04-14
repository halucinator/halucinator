from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_spi import STM32F4SPI

WORDSIZE = 4
NUM_WORDS = 1
HW_ADDR = 0x12000
HSPI = 10
BUFF_ADDR = 0x40000
# With current typing, SPIPublisher.read returns a 'str' and
# SPIPublisher.write expects a 'bytes'. The latter type is important
# in the other direction as well -- read_memory has to return a
# 'bytes' because of the 'assert isinstance(..., bytes)' inserted to
# allow typechecking to proceed (and because Avatar *does* return a
# 'bytes' for those calls). So we need both of these for now.
DATA_FROM_PERIPHERAL = "qwertyasdfg"
DATA_TO_PERIPHERAL = b"qwertyasdfg"


def read_memory_fake(
    address, wordsize=WORDSIZE, num_words=NUM_WORDS, raw=False
):
    if address == HSPI:
        assert wordsize == WORDSIZE
        assert num_words == NUM_WORDS
        assert not raw
        return HW_ADDR
    elif address == BUFF_ADDR:
        assert wordsize == 1
        assert num_words == len(DATA_TO_PERIPHERAL)
        assert raw
        return DATA_TO_PERIPHERAL
    else:
        return mock.DEFAULT


@pytest.fixture
def spi_qemu_mock(qemu_mock):
    qemu_mock.read_memory = mock.Mock(side_effect=read_memory_fake)
    return qemu_mock


@pytest.fixture
def spi():
    mock_model = mock.Mock()
    return STM32F4SPI(mock_model)


class TestSTM32F4SPI:
    def test_hal_ok_just_returns_True_and_zero(self, spi):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Init (
        #   SPI_HandleTypeDef * hspi
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group1.html#gaadb9d40e710c714d96b2501996658c44
        continue_, ret_val = spi.hal_ok(None, None)
        assert continue_
        assert ret_val == 0

    def test_hal_ok_2_just_returns_True_and_zero(self, spi):
        # Associated HAL fuction declaration
        # void
        # HAL_SPI_MspDeInit (
        #   SPI_HandleTypeDef * hspi
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group1.html#gabadc4d4974af1afd943e8d13589068e1
        continue_, ret_val = spi.hal_ok_2(None, None)
        assert continue_
        assert ret_val == 0

    def test_get_state_just_returns_True_and_ready(self, spi):
        # Associated HAL fuction declaration
        # HAL_SPI_StateTypeDef
        # HAL_SPI_GetState (
        #   SPI_HandleTypeDef * hspi
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group3.html#ga4e599e7fac80bb2eb0fd3f1737e50a5e
        READY = 0x20
        continue_, ret_val = spi.get_state(None, None)
        assert continue_
        assert ret_val == READY

    def test_handle_tx_reads_hw_address_and_data_from_qemu_memory_and_writes_it_to_model(
        self, spi, spi_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Transmit (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size,
        #   uint32_t            Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga02ec86e05d0702387c221f90b6f041a2
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Transmit_IT (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#gafbb309aa738bb3296934fb1a39ffbf40
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Transmit_DMA (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga6aebe304396c3e18b55f926dae0dadcb
        spi.model.write = mock.Mock()
        spi.model.read = mock.Mock()
        set_arguments(
            spi_qemu_mock, [HSPI, BUFF_ADDR, len(DATA_TO_PERIPHERAL)]
        )
        continue_, ret_val = spi.handle_tx(spi_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        spi.model.write.assert_called_with(HW_ADDR, DATA_TO_PERIPHERAL)
        spi.model.read.assert_not_called()

    def test_handle_rx_reads_data_from_model_and_writes_it_to_qemu_memory_at_hw_address(
        self, spi, spi_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Receive (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size,
        #   uint32_t            Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#gafdf43dbe4e5ef225bed6650b6e8c6313
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Receive_IT (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#gaaae0af2e2db7e7549b52b020a18f6168
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_Receive_DMA (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga626bb2ec54e7b6ff9bd5d807ae6e6e24
        spi.model.read = mock.Mock(return_value=DATA_FROM_PERIPHERAL)
        spi.model.write = mock.Mock()
        set_arguments(
            spi_qemu_mock, [HSPI, BUFF_ADDR, len(DATA_FROM_PERIPHERAL)]
        )
        spi_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = spi.handle_rx(spi_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        spi.model.read.assert_called_with(
            HW_ADDR, len(DATA_FROM_PERIPHERAL), block=True
        )
        spi_qemu_mock.write_memory.assert_called_with(
            BUFF_ADDR,
            1,
            DATA_FROM_PERIPHERAL,
            len(DATA_FROM_PERIPHERAL),
            raw=True,
        )
        spi.model.write.assert_not_called()

    @pytest.mark.xfail
    def test_handle_txrx_reads_hw_address_and_data_from_qemu_memory_writes_it_to_model_read_new_data_from_model_writes_it_to_qemu_memory(
        self, spi, spi_qemu_mock
    ):
        # This test should pass when https://gitlab.com/METIS/halucinator/-/issues/44 is resolved.
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_TransmitReceive (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pTxData,
        #   uint8_t *           pRxData,
        #   uint16_t            Size,
        #   uint32_t            Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga7c3106fe01493a33b08e5c617f45aeb1
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_TransmitReceive_IT (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pTxData,
        #   uint8_t *           pRxData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga518c43d8323499451e7f4782a9dc6e32
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SPI_TransmitReceive_DMA (
        #   SPI_HandleTypeDef * hspi,
        #   uint8_t *           pTxData,
        #   uint8_t *           pRxData,
        #   uint16_t            Size
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__spi__exported__functions__group2.html#ga228553c64b10b8dade9fee525a8a489d
        TRANSMIT_ADDRESS = 0x1000
        RECEIVE_ADDRESS = 0x2000
        NEW_DATA = "12345678901"  # from peripheral
        assert len(DATA_TO_PERIPHERAL) == len(NEW_DATA)
        spi.model.write = mock.Mock()
        spi.model.read = mock.Mock(return_value=NEW_DATA)
        set_arguments(
            spi_qemu_mock,
            [HSPI, TRANSMIT_ADDRESS, RECEIVE_ADDRESS, len(NEW_DATA)],
        )
        spi_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = spi.handle_txrx(spi_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        spi.model.write.assert_called_with(HW_ADDR, DATA_TO_PERIPHERAL)
        spi.model.read.assert_called_with(HW_ADDR, len(NEW_DATA), block=True)
        spi_qemu_mock.write_memory.assert_called_with(
            BUFF_ADDR, 1, NEW_DATA, len(NEW_DATA), raw=True
        )
