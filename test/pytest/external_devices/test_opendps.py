"""
Tests for halucinator.external_devices.opendps
"""

from unittest import mock

import pytest

from halucinator.external_devices.opendps import UARTDPSController, main


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def controller(mock_ioserver):
    return UARTDPSController(mock_ioserver)


class TestUARTDPSControllerInit:
    def test_stores_ioserver(self, mock_ioserver):
        ctrl = UARTDPSController(mock_ioserver)
        assert ctrl.ioserver is mock_ioserver

    def test_last_packet_none(self, controller):
        assert controller.last_packet is None

    def test_send_packet_none(self, controller):
        assert controller.send_packet is None

    def test_registers_write_topic(self, mock_ioserver):
        UARTDPSController(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.UARTPublisher.write", mock.ANY
        )


class TestUARTDPSControllerWriteHandler:
    def test_stores_decoded_packet(self, controller, mock_ioserver):
        msg = {"chars": b"hello"}
        controller.write_handler(mock_ioserver, msg)
        assert controller.last_packet == "hello"

    def test_decodes_latin1(self, controller, mock_ioserver):
        msg = {"chars": b"\xe9\xe8"}
        controller.write_handler(mock_ioserver, msg)
        assert controller.last_packet == "\xe9\xe8"

    def test_overwrites_previous_packet(self, controller, mock_ioserver):
        controller.write_handler(mock_ioserver, {"chars": b"first"})
        controller.write_handler(mock_ioserver, {"chars": b"second"})
        assert controller.last_packet == "second"


class TestUARTDPSControllerSendData:
    def test_sends_correct_message(self, controller, mock_ioserver):
        controller.send_data(0x40013800, "test_data")
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UARTPublisher.rx_data",
            {"id": 0x40013800, "chars": "test_data"},
        )

    def test_sends_with_different_id(self, controller, mock_ioserver):
        controller.send_data(42, "abc")
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UARTPublisher.rx_data",
            {"id": 42, "chars": "abc"},
        )


class TestOpenDPSMain:
    def test_main_creates_ioserver(self):
        """Test that main() creates IOServer and registers handler."""
        with mock.patch(
            "halucinator.external_devices.opendps.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log"
        ), mock.patch("sys.argv", ["opendps"]):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            # Make io_server.start() raise to prevent entering the tight loop
            mock_io_inst.start.side_effect = KeyboardInterrupt

            try:
                main()
            except KeyboardInterrupt:
                pass

            MockIO.assert_called_once_with(5556, 5555)

    def test_main_with_custom_args(self):
        with mock.patch(
            "halucinator.external_devices.opendps.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log"
        ), mock.patch("sys.argv", ["opendps", "-r", "7777", "-t", "8888"]):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            mock_io_inst.start.side_effect = KeyboardInterrupt

            try:
                main()
            except KeyboardInterrupt:
                pass

            MockIO.assert_called_once_with("7777", "8888")

    def test_main_send_packet_path(self):
        """Test send_packet path directly."""
        mock_io_inst = mock.Mock()
        ctrl = UARTDPSController(mock_io_inst)
        ctrl.send_packet = "test_response"
        ctrl.send_data(0x40013800, ctrl.send_packet)
        ctrl.send_packet = None
        mock_io_inst.send_msg.assert_called_once()
