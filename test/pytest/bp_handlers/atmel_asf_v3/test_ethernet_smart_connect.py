from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.ethernet_smart_connect import (
    EthernetSmartConnect,
)

ID = "ksz8851"

NUMBER_OF_FRAMES = 2
SIZE_1ST_FRAME = 16
FRAME_DATA = b"\x25" * SIZE_1ST_FRAME

# The location of ADDR_15882 is special-cased in the eth_process function -
# https://gitlab.com/METIS/halucinator/-/blob/main/src/halucinator/bp_handlers/atmel_asf_v3/ethernet_smart_connect.py#L43
ADDR_15882_LOCATION = 0x200000E8
BUFFER_ADDRESS = 0x1500
BUFFER_LEN = 128
BUFFER_LEN_SHORT = 8
ZERO_FRAMES = ZERO_LENGTH = 0

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    ADDR_15882_LOCATION: [BUFFER_ADDRESS],
    BUFFER_ADDRESS: [FRAME_DATA, 1, SIZE_1ST_FRAME, True],
}


@pytest.fixture
def esc_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def esc():
    mock_model = mock.Mock()
    return EthernetSmartConnect(mock_model)


class TestEthernetSmartConnect:
    def test_eth_process_does_not_read_frame_when_no_frames_available(
        self, esc, esc_qemu_mock
    ):
        esc.model.get_frame_info = mock.Mock(
            return_value=(ZERO_FRAMES, ZERO_LENGTH)
        )
        esc.model.get_rx_frame_only = mock.Mock()
        continue_, ret_val = esc.eth_process(esc_qemu_mock, None)
        assert not continue_
        assert ret_val is None
        esc.model.get_frame_info.assert_called_once_with(ID)
        esc.model.get_rx_frame_only.assert_not_called()

    def test_eth_process_reads_frame_and_writes_to_qemu_memory_when_available(
        self, esc, esc_qemu_mock
    ):
        REG_R0_INIT = 0x2500
        REG_R1_INIT = 0x2600
        REG_LR_INIT = 0xFFFF
        REG_PC_INIT = 0x1400
        REG_LR_EXPECTED = 0x1401
        IP64_ETH_INTERFACE_INPUT_ADDRESS = 0x3600
        REG_PC_EXPECTED = 0x3601
        assert REG_LR_EXPECTED == REG_PC_INIT | 1
        assert REG_PC_EXPECTED == IP64_ETH_INTERFACE_INPUT_ADDRESS | 1
        esc_qemu_mock.avatar.callables = {
            "ip64_eth_interface_input": IP64_ETH_INTERFACE_INPUT_ADDRESS,
        }
        # Set initial values for QEMU registers to be sure that they are really changed after
        # calling the eth_process function
        esc_qemu_mock.regs.r0 = REG_R0_INIT
        esc_qemu_mock.regs.r1 = REG_R1_INIT
        esc_qemu_mock.regs.lr = REG_LR_INIT
        esc_qemu_mock.regs.pc = REG_PC_INIT
        esc.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        esc.model.get_rx_frame_only = mock.Mock(return_value=FRAME_DATA)
        esc_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = esc.eth_process(esc_qemu_mock, None)
        assert continue_
        assert ret_val is None
        esc.model.get_frame_info.assert_called_once_with(ID)
        esc.model.get_rx_frame_only.assert_called_once_with(ID)
        esc_qemu_mock.write_memory.assert_called_once_with(
            BUFFER_ADDRESS, 1, FRAME_DATA, len(FRAME_DATA), raw=True
        )
        assert esc_qemu_mock.regs.r0 == BUFFER_ADDRESS
        assert esc_qemu_mock.regs.r1 == len(FRAME_DATA)
        assert esc_qemu_mock.regs.lr == REG_LR_EXPECTED
        assert esc_qemu_mock.regs.pc == REG_PC_EXPECTED

    def test_read_does_not_read_frame_when_no_frames_available(
        self, esc, esc_qemu_mock
    ):
        # Associated HAL fuction declaration
        # int
        # ksz8851snl_read (
        #   uint8_t * ,
        #   uint16_t
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/ksz8851snl-ip64-driver_8h.html#aa5cca14316b53950a7acd14046220a58
        esc.model.get_frame_info = mock.Mock(
            return_value=(ZERO_FRAMES, ZERO_LENGTH)
        )
        esc.model.get_rx_frame_and_time = mock.Mock()
        continue_, ret_val = esc.read(esc_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        esc.model.get_frame_info.assert_called_once_with(ID)
        esc.model.get_rx_frame_and_time.assert_not_called()

    def test_read_does_not_write_frame_to_qemu_memory_when_frame_len_greater_than_buffer(
        self, esc, esc_qemu_mock
    ):
        # Associated HAL fuction declaration
        # int
        # ksz8851snl_read (
        #   uint8_t * ,
        #   uint16_t
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/ksz8851snl-ip64-driver_8h.html#aa5cca14316b53950a7acd14046220a58
        set_arguments(esc_qemu_mock, [BUFFER_ADDRESS, BUFFER_LEN_SHORT])
        esc.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        esc.model.get_rx_frame_and_time = mock.Mock(
            return_value=(FRAME_DATA, 0)
        )
        esc_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = esc.read(esc_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        esc.model.get_frame_info.assert_called_once_with(ID)
        esc.model.get_rx_frame_and_time.assert_called_once_with(ID)
        esc_qemu_mock.write_memory.assert_not_called()

    def test_read_writes_frame_to_qemu_memory_when_buffer_has_enough_memory_to_store_frame(
        self, esc, esc_qemu_mock
    ):
        # Associated HAL fuction declaration
        # int
        # ksz8851snl_read (
        #   uint8_t * ,
        #   uint16_t
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/ksz8851snl-ip64-driver_8h.html#aa5cca14316b53950a7acd14046220a58
        set_arguments(esc_qemu_mock, [BUFFER_ADDRESS, BUFFER_LEN])
        esc.model.get_frame_info = mock.Mock(
            return_value=(NUMBER_OF_FRAMES, SIZE_1ST_FRAME)
        )
        esc.model.get_rx_frame_and_time = mock.Mock(
            return_value=(FRAME_DATA, 0)
        )
        esc_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = esc.read(esc_qemu_mock, None)
        assert continue_
        assert ret_val == len(FRAME_DATA)
        esc.model.get_frame_info.assert_called_once_with(ID)
        esc.model.get_rx_frame_and_time.assert_called_once_with(ID)
        esc_qemu_mock.write_memory.assert_called_once_with(
            BUFFER_ADDRESS, 1, FRAME_DATA, len(FRAME_DATA), raw=True
        )

    def test_send_reads_data_from_qemu_memory_and_sends_it_correctly(
        self, esc, esc_qemu_mock
    ):
        # Associated HAL fuction declaration
        # int
        # ksz8851snl_send (
        #   const uint8_t * ,
        #   uint16_t
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/samd21/html/ksz8851snl-ip64-driver_8h.html#a9a640183c267d07f23e8d89f995c9a3f
        set_arguments(esc_qemu_mock, [BUFFER_ADDRESS, SIZE_1ST_FRAME])
        esc.model.tx_frame = mock.Mock()
        continue_, ret_val = esc.send(esc_qemu_mock, None)
        assert continue_
        assert ret_val == SIZE_1ST_FRAME
        esc.model.tx_frame.assert_called_once_with(ID, FRAME_DATA)

    def test_return_ok_just_returns_zero(self, esc):
        # Associated HAL fuction declaration
        # uint32_t
        # ksz8851snl_send (
        #   void
        # )
        # The under test function's description can be found here -
        # https://github.com/particle-iot/freertos/blob/master/FreeRTOS-Plus/Source/FreeRTOS-Plus-TCP/portable/NetworkInterface/ksz8851snl/ksz8851snl.h#L61
        continue_, ret_val = esc.return_ok(esc_qemu_mock, None)
        assert continue_
        assert ret_val == 0
