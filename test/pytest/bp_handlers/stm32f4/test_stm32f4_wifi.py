from unittest import mock

import pytest
from arm_helpers import set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_wifi import (
    WIFI_CONNECTED,
    WIFI_IDLE,
    WIFI_OFF,
    STM32F4Wifi,
)

PC_REG_SENTINEL_VALUE = 0x20480


@pytest.fixture
def wifi():
    mock_model = mock.Mock()
    return STM32F4Wifi(mock_model)


class TestSTM32F4GPIO:
    @pytest.mark.xfail
    def test_wifi_init_starts_timer_and_returns_zero(self, wifi):
        # Associated Wi-Fi fuction declaration
        # WiFi_Status_t
        # wifi_init (
        #   wifi_config * config
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/teams/ST/code/X_NUCLEO_IDW01M1/docs/tip/group__NUCLEO__WIFI__INTERFACE__Private__Functions.html#ga1b350b3ba43790d7259a102e62ba92db
        assert False
        TIMER_ID = 0x40000400
        TIMER_IRQ = 45
        TIMER_RATE = 2
        wifi.timer.start_timer = mock.Mock()
        continue_, ret_val = wifi.wifi_init(None, None)
        assert continue_
        assert ret_val == 0
        wifi.timer.start_timer.assert_called_with(
            TIMER_ID, TIMER_IRQ, TIMER_RATE
        )

    def test_wifi_wakeup_just_returns_True_and_zero(self, wifi):
        # Associated Wi-Fi fuction declaration
        # WiFi_Status_t
        # wifi_wakeup (
        #   wifi_bool wakeup
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/teams/ST/code/X_NUCLEO_IDW01M1/docs/tip/group__NUCLEO__WIFI__INTERFACE__Private__Functions.html#ga63cdc38d6fe0450da0abbc88aa3243cb
        continue_, ret_val = wifi.wifi_wakeup(None, None)
        assert continue_
        assert ret_val == 0

    def test_receive_data_just_returns_True_and_zero(self, wifi):
        continue_, ret_val = wifi.receive_data(None, None)
        assert continue_
        assert ret_val == 0

    def test_wifi_ap_start_just_returns_True_and_zero(self, wifi):
        # Associated Wi-Fi fuction declaration
        # WiFi_Status_t
        # wifi_ap_start (
        #   uint8_t * ssid,
        #   uint8_t channel_num
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/teams/ST/code/X_NUCLEO_IDW01M1/docs/tip/group__NUCLEO__WIFI__INTERFACE__Private__Functions.html#gac8c1347831ce2cdadc366d86c988e215
        continue_, ret_val = wifi.wifi_ap_start(None, None)
        assert continue_
        assert ret_val == 0

    def test_wifi_socket_server_open_listens_provided_port(
        self, qemu_mock, wifi
    ):
        # Associated Wi-Fi fuction declaration
        # WiFi_Status_t
        # wifi_socket_server_open (
        #   uint32_t port_number,
        #   uint8_t * protocol
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/teams/ST/code/X_NUCLEO_IDW01M1/docs/tip/group__NUCLEO__WIFI__INTERFACE__Private__Functions.html#ga71ce648637b13e6190d982471ca0ad2d
        PORT = 120
        set_arguments(qemu_mock, [PORT])
        wifi.model.listen = mock.Mock()
        continue_, ret_val = wifi.wifi_socket_server_open(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        wifi.model.listen.assert_called_with(PORT)

    @pytest.mark.parametrize(
        "data", [b"123456", b"aaaabbbbcccc", b"\x01\x02\x03\x0a\x14\x20\x30",]
    )
    @pytest.mark.parametrize("address", [1024, 5000, 8192])
    def test_wifi_socket_server_write_sends_data_correctly(
        self, qemu_mock, wifi, address, data
    ):
        # Associated Wi-Fi fuction declaration
        # int
        # wifi_socket_server_write (
        #   uint16_t 	DataLength,
        #   char * pData
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/teams/ST/code/X_NUCLEO_IDW01M1/docs/tip/wifi__driver_8c.html#af91d957c2bf33e8eaacace55ccd55aac
        length = len(data)
        set_arguments(qemu_mock, [length, address])
        qemu_mock.read_memory = mock.Mock(return_value=data)
        wifi.model.tx_packet = mock.Mock()
        continue_, ret_val = wifi.wifi_socket_server_write(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        qemu_mock.read_memory.assert_called_with(address, 1, length, raw=True)
        wifi.model.tx_packet.assert_called_with(data)

    def test_wifi_systick_isr_just_returns_True_and_zero(self, wifi):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_SysTick_Isr (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga0ba72c4c0faa8ea38e83952248af567d
        continue_, ret_val = wifi.wifi_systick_isr(None, None)
        assert continue_
        assert ret_val == 0

    @pytest.mark.parametrize("wifi_conn", [None, True])
    def test_wifi_tim_handler_if_wifi_is_off_and_sock_is_not_none_then_no_callback_is_needed(
        self, qemu_mock, wifi, wifi_conn
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        wifi.wifi_state = WIFI_OFF
        wifi.model.sock = True
        wifi.model.conn = wifi_conn
        qemu_mock.regs.pc = PC_REG_SENTINEL_VALUE
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        wifi.model.get_rx_packet.assert_not_called()
        assert qemu_mock.regs.pc == PC_REG_SENTINEL_VALUE

    @pytest.mark.parametrize("wifi_sock", [None, True])
    def test_wifi_tim_handler_if_wifi_is_idle_and_there_is_no_connection_then_no_callback_is_needed(
        self, qemu_mock, wifi, wifi_sock
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        wifi.wifi_state = WIFI_IDLE
        wifi.model.sock = wifi_sock
        wifi.model.conn = None
        qemu_mock.regs.pc = PC_REG_SENTINEL_VALUE
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert continue_
        assert ret_val == 0
        wifi.model.get_rx_packet.assert_not_called()
        assert qemu_mock.regs.pc == PC_REG_SENTINEL_VALUE

    def test_wifi_tim_handler_forwards_to_ind_wifi_connected_when_wifi_state_is_off_and_no_open_socket(
        self, qemu_mock, wifi
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        CALLABLE_NAME = "ind_wifi_connected"
        CALLABLE_ADDRESS = 0x1000
        wifi.wifi_state = WIFI_OFF
        wifi.model.sock = None
        wifi.model.get_rx_packet = mock.Mock(return_value=None)
        qemu_mock.avatar.callables = {CALLABLE_NAME: CALLABLE_ADDRESS}
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        assert wifi.wifi_state == WIFI_IDLE
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS
        wifi.model.get_rx_packet.assert_not_called()

    def test_wifi_tim_handler_sets_qemu_pc_register_correctly_when_wifi_state_is_idle_and_not_connected(
        self, qemu_mock, wifi
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        CALLABLE_NAME = "ind_socket_server_client_joined"
        CALLABLE_ADDRESS = 0x2000
        wifi.wifi_state = WIFI_IDLE
        wifi.model.conn = True
        wifi.model.get_rx_packet = mock.Mock(return_value=None)
        qemu_mock.avatar.callables = {CALLABLE_NAME: CALLABLE_ADDRESS}
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        assert wifi.wifi_state == WIFI_CONNECTED
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS
        wifi.model.get_rx_packet.assert_not_called()

    def test_wifi_tim_handler_sets_qemu_pc_register_correctly_when_wifi_state_is_connected_but_connection_lost(
        self, qemu_mock, wifi
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        CALLABLE_NAME = "ind_socket_server_client_left"
        CALLABLE_ADDRESS = 0x4000
        wifi.wifi_state = WIFI_CONNECTED
        wifi.model.conn = None
        wifi.model.get_rx_packet = mock.Mock(return_value=None)
        qemu_mock.avatar.callables = {CALLABLE_NAME: CALLABLE_ADDRESS}
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        assert wifi.wifi_state == WIFI_IDLE
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS
        wifi.model.get_rx_packet.assert_called()

    def test_wifi_tim_handler_writes_qemu_memory_and_sets_qemu_pc_register_correctly_when_data_received(
        self, qemu_mock, wifi
    ):
        # Associated Wi-Fi fuction declaration
        # void
        # Wifi_TIM_Handler (
        #   void
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/docs/tip/group__NUCLEO__WIFI__MODULE__Private__Functions.html#ga93fd276eecfb702d02fa796740a6a6f2
        CALLABLE_NAME = "ind_wifi_socket_data_received"
        CALLABLE_ADDRESS = 0x5000
        DATA = "abcdefgh"
        DATA_WITH_END = DATA + "\0"
        RX_DATA_BUF = 0x200F0000
        wifi.wifi_state = WIFI_CONNECTED
        wifi.model.get_rx_packet = mock.Mock(return_value=DATA)
        qemu_mock.avatar.callables = {CALLABLE_NAME: CALLABLE_ADDRESS}
        qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = wifi.wifi_tim_handler(qemu_mock, None)
        assert not continue_
        assert ret_val is None
        assert wifi.wifi_state == WIFI_CONNECTED
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS
        wifi.model.get_rx_packet.assert_called()
        qemu_mock.write_memory.assert_called_with(
            RX_DATA_BUF, 1, DATA_WITH_END, len(DATA_WITH_END), raw=True
        )
        # Associated Wi-Fi callback fuction declaration
        # void
        # ind_wifi_socket_data_received (
        #   uint8_t socket_id,
        #   uint8_t * data_ptr,
        #   uint32_t message_size,
        #   uint32_t chunk_size
        # )
        # The under test function's description can be found here -
        # https://os.mbed.com/users/scsims/code/X_NUCLEO_IDW01M1_AP/raw-file/bd9db471d47d/Spwf/inc/wifi_interface.h/
        assert qemu_mock.regs.r0 == 0
        assert qemu_mock.regs.r1 == RX_DATA_BUF
        assert qemu_mock.regs.r2 == len(DATA_WITH_END)
        assert qemu_mock.regs.r3 == len(DATA_WITH_END)
        assert qemu_mock.regs.pc == CALLABLE_ADDRESS
