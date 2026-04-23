from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_eth import STM32F4Ethernet

HETH_PTR = 0x1000
HETH_ID = 100
WORDSIZE = 4
PHY_VALUE_PTR = 0x2000
TX_DMA_DESC = 0x1200
RX_DESC_OFFSET = 40
HETH_PTR_RX_DESC_ADDRESS = 0x1028
assert HETH_PTR_RX_DESC_ADDRESS == HETH_PTR + RX_DESC_OFFSET
NEXT_RX_DESC_OFFSET = 12
RX_DESC_ADDRESS = 0x1400
NEXT_RX_DESC_ADDRESS = 0x1800
DMA_DESC_OFFSET = 44
HETH_PTR_DMA_DESC_ADDRESS = 0x102C
assert HETH_PTR_DMA_DESC_ADDRESS == HETH_PTR + DMA_DESC_OFFSET
DMA_INFO_OFFSET = 48
HETH_PTR_DMA_INFO_ADDRESS = 0x1030
assert HETH_PTR_DMA_INFO_ADDRESS == HETH_PTR + DMA_INFO_OFFSET
TX_FRAME_PTR = 0x3600
TX_FRAME_PTR_OFFSET = 8
TX_DMA_DESC_TX_FRAME_PTR_ADDRESS = 0x1208
assert TX_DMA_DESC_TX_FRAME_PTR_ADDRESS == TX_DMA_DESC + TX_FRAME_PTR_OFFSET
RX_DESC_ADDRESS_TX_FRAME_PTR_ADDRESS = 0x1408
assert (
    RX_DESC_ADDRESS_TX_FRAME_PTR_ADDRESS
    == RX_DESC_ADDRESS + TX_FRAME_PTR_OFFSET
)
RX_DESC_ADDRESS_NEXT_RX_DESC_ADDRESS = 0x140C
assert (
    RX_DESC_ADDRESS_NEXT_RX_DESC_ADDRESS
    == RX_DESC_ADDRESS + NEXT_RX_DESC_OFFSET
)
FRAME = b"qwertyuiopasdfgh"
FRAME_LEN = len(FRAME)

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    HETH_PTR: [HETH_ID],
    HETH_PTR_DMA_DESC_ADDRESS: [TX_DMA_DESC],
    TX_DMA_DESC_TX_FRAME_PTR_ADDRESS: [TX_FRAME_PTR],
    RX_DESC_ADDRESS_TX_FRAME_PTR_ADDRESS: [TX_FRAME_PTR],
    HETH_PTR_RX_DESC_ADDRESS: [RX_DESC_ADDRESS],
    RX_DESC_ADDRESS_NEXT_RX_DESC_ADDRESS: [NEXT_RX_DESC_ADDRESS],
    TX_FRAME_PTR: [FRAME, 1, FRAME_LEN, True],
}


@pytest.fixture
def ethernet_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def ethernet():
    mock_model = mock.Mock()
    return STM32F4Ethernet(mock_model)


class TestSTM32F4Ethernet:
    def test_handle_tx_reads_frame_from_qemu_memory_and_sends_it_correctly(
        self, ethernet, ethernet_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_ETH_TransmitFrame (
        #   ETH_HandleTypeDef * heth,
        #   uint32_t FrameLength
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__eth__exported__functions__group2.html#ga09dc8287c10b5882ce7adf0fd7ba3cda
        set_arguments(ethernet_qemu_mock, [HETH_PTR, FRAME_LEN])
        ethernet.model.tx_frame = mock.Mock()
        continue_, ret_val = ethernet.handle_tx(ethernet_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        ethernet.model.tx_frame.assert_called_with(HETH_ID, FRAME)

    def test_handle_rx_writes_empty_frame_to_qemu_memory_when_no_frame_received(
        self, ethernet, ethernet_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_ETH_GetReceivedFrame (
        #   ETH_HandleTypeDef * heth
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__eth__exported__functions__group2.html#gab630598f1bfd6073ca77c8a04a28b121
        set_arguments(ethernet_qemu_mock, [HETH_PTR])
        ethernet.model.get_rx_frame_only = mock.Mock(return_value=None)
        ethernet_qemu_mock.write_memory = mock.Mock()
        expected_frame_info = b"\x00" * 20
        continue_, ret_val = ethernet.handle_rx(ethernet_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        ethernet.model.get_rx_frame_only.assert_called_with(HETH_ID)
        ethernet_qemu_mock.write_memory.assert_called_with(
            HETH_PTR_DMA_INFO_ADDRESS,
            1,
            expected_frame_info,
            len(expected_frame_info),
            raw=True,
        )

    @pytest.mark.xfail
    def test_handle_rx_writes_frame_to_qemu_memory_correctly_after_receiving(
        self, ethernet, ethernet_qemu_mock
    ):
        # The function xfailed because of https://gitlab.com/METIS/halucinator/-/issues/45
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_ETH_GetReceivedFrame (
        #   ETH_HandleTypeDef * heth
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__eth__exported__functions__group2.html#gab630598f1bfd6073ca77c8a04a28b121
        set_arguments(ethernet_qemu_mock, [HETH_PTR])
        ethernet.model.get_rx_frame_only = mock.Mock(return_value=FRAME)
        ethernet_qemu_mock.write_memory = mock.Mock()
        expected_frame_info = (
            RX_DESC_ADDRESS.to_bytes(4, "little")
            + RX_DESC_ADDRESS.to_bytes(4, "little")
            + (1).to_bytes(4, "little")
            + FRAME_LEN.to_bytes(4, "little")
            + TX_FRAME_PTR.to_bytes(4, "little")
        )
        continue_, ret_val = ethernet.handle_rx(ethernet_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        ethernet.model.get_rx_frame_only.assert_called_with(HETH_ID)
        ethernet_qemu_mock.write_memory.assert_any_call(
            HETH_PTR_DMA_INFO_ADDRESS,
            1,
            expected_frame_info,
            len(expected_frame_info),
            raw=True,
        )
        ethernet_qemu_mock.write_memory.assert_any_call(
            TX_FRAME_PTR, 1, FRAME, FRAME_LEN, raw=True
        )
        ethernet_qemu_mock.write_memory.assert_any_call(
            HETH_PTR_RX_DESC_ADDRESS, 4, NEXT_RX_DESC_ADDRESS, 1
        )

    @pytest.mark.parametrize(
        "phy_reg,phy_value",
        [(7, 0x785D), (0x20, 0x1A5), (0x1B, 0x20), (0x21, 0x4E00)],
    )
    @pytest.mark.xfail
    def test_write_phy_reads_value_from_qemu_memory_and_writes_to_register_correctly(
        self, ethernet, qemu_mock, phy_reg, phy_value
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_ETH_WritePHYRegister (
        #   ETH_HandleTypeDef * heth,
        #   uint16_t PHYReg,
        #   uint32_t RegValue
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__eth__exported__functions__group2.html#ga3e46a062a5a97b8b5920bbd5bfdd09e7
        set_arguments(qemu_mock, [0, phy_reg, phy_value])
        qemu_mock.read_memory = mock.Mock(return_value=phy_value)
        continue_, ret_val = ethernet.write_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert ethernet.phy_registers[phy_reg] == phy_value

    @pytest.mark.parametrize(
        "phy_reg,phy_value",
        [(1, 0x786D), (0x10, 0x115), (0x11, 0), (0x12, 0x2C00)],
    )
    def test_read_phy_gives_these_specific_default_values_for_registers(
        self, ethernet, qemu_mock, phy_reg, phy_value
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_ETH_WritePHYRegister (
        #   ETH_HandleTypeDef * heth,
        #   uint16_t PHYReg,
        #   uint32_t RegValue
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__eth__exported__functions__group2.html#ga3e46a062a5a97b8b5920bbd5bfdd09e7
        set_arguments(qemu_mock, [0, phy_reg, PHY_VALUE_PTR])
        qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = ethernet.read_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.write_memory.assert_called_with(
            PHY_VALUE_PTR, WORDSIZE, phy_value
        )

    @pytest.mark.xfail
    def test_write_to_and_read_from_phy_registers_work_correctly(
        self, ethernet, qemu_mock
    ):
        # Associated HAL fuction declaration
        # See above
        PHY_DEST_VALUE_PTR1 = 0x4200
        PHY_DEST_VALUE_PTR2 = 0x4400
        PHY_REG1 = 0x22
        PHY_REG2 = 0x1C
        PHY_REG1_VALUE = 0x4444
        PHY_REG2_VALUE = 0x804D
        # Assign PHY_REG1 = PHY_REG1_VALUE
        set_arguments(qemu_mock, [0, PHY_REG1, PHY_REG1_VALUE])
        continue_, ret_val = ethernet.write_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert ethernet.phy_registers[PHY_REG1] == PHY_REG1_VALUE
        # Assign PHY_REG2 = PHY_REG2_VALUE
        set_arguments(qemu_mock, [0, PHY_REG2, PHY_REG2_VALUE])
        continue_, ret_val = ethernet.write_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert ethernet.phy_registers[PHY_REG2] == PHY_REG2_VALUE
        # Check that REG1 reads as REG1_VALUE
        qemu_mock.write_memory = mock.Mock()
        set_arguments(qemu_mock, [0, PHY_REG1, PHY_DEST_VALUE_PTR1])
        continue_, ret_val = ethernet.read_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.write_memory.assert_called_once_with(
            PHY_DEST_VALUE_PTR1, WORDSIZE, PHY_REG1_VALUE
        )
        # Check that REG2 reads as REG2_VALUE
        qemu_mock.write_memory = mock.Mock()
        set_arguments(qemu_mock, [0, PHY_REG2, PHY_DEST_VALUE_PTR2])
        continue_, ret_val = ethernet.read_phy(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.write_memory.assert_called_once_with(
            PHY_DEST_VALUE_PTR2, WORDSIZE, PHY_REG2_VALUE
        )
