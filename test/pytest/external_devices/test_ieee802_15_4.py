"""
Tests for halucinator.external_devices.IEEE802_15_4
"""

from unittest import mock

import pytest

from halucinator.external_devices.IEEE802_15_4 import IEEE802_15_4


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def hub():
    return IEEE802_15_4()


class TestIEEE802Init:
    def test_empty_init(self):
        hub = IEEE802_15_4()
        assert hub.ioservers == []
        assert hub.host_socket is None

    def test_init_with_servers(self):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub = IEEE802_15_4(ioservers=[s1, s2])
        assert len(hub.ioservers) == 2
        assert s1.register_topic.call_count == 1
        assert s2.register_topic.call_count == 1


class TestIEEE802AddServer:
    def test_adds_server(self, hub, mock_ioserver):
        hub.add_server(mock_ioserver)
        assert mock_ioserver in hub.ioservers

    def test_registers_topic(self, hub, mock_ioserver):
        hub.add_server(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.IEEE802_15_4.tx_frame", hub.received_frame
        )

    def test_adds_multiple_servers(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)
        assert len(hub.ioservers) == 2


class TestIEEE802ReceivedFrame:
    def test_forwards_to_other_servers(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)

        msg = {"id": "rf233", "frame": b"\x01\x02"}
        hub.received_frame(s1, msg)
        # Should forward to s2 but not s1
        s2.send_msg.assert_called_once_with(
            "Peripheral.IEEE802_15_4.rx_frame", msg
        )
        s1.send_msg.assert_not_called()

    def test_forwards_from_none_to_all(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)

        msg = {"id": "rf233", "frame": b"\x01"}
        hub.received_frame(None, msg)
        s1.send_msg.assert_called_once()
        s2.send_msg.assert_called_once()

    def test_forwards_to_host_socket(self, hub):
        hub.host_socket = mock.Mock()
        s1 = mock.Mock()
        hub.add_server(s1)

        msg = {"id": "rf233", "frame": b"\xAB\xCD"}
        hub.received_frame(s1, msg)
        hub.host_socket.send.assert_called_once_with(b"\xAB\xCD")

    def test_no_host_socket(self, hub):
        s1 = mock.Mock()
        hub.add_server(s1)
        msg = {"id": "rf233", "frame": b"\x01"}
        # Should not raise
        hub.received_frame(s1, msg)


class TestIEEE802Shutdown:
    def test_shuts_down_all_servers(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)
        hub.shutdown()
        s1.shutdown.assert_called_once()
        s2.shutdown.assert_called_once()

    def test_shutdown_empty(self, hub):
        hub.shutdown()  # Should not raise


class TestIEEE802Main:
    def test_main_port_mismatch(self, capsys):
        from halucinator.external_devices.IEEE802_15_4 import main
        with mock.patch(
            "sys.argv",
            ["ieee802", "-r", "5556", "5558", "-t", "5555"],
        ):
            with pytest.raises(SystemExit):
                main()

    def test_main_creates_hub(self):
        from halucinator.external_devices.IEEE802_15_4 import main

        with mock.patch("sys.argv", [
            "ieee802", "-r", "5556", "5558", "-t", "5555", "5557",
        ]), mock.patch(
            "halucinator.external_devices.IEEE802_15_4.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ), mock.patch(
            "halucinator.external_devices.IEEE802_15_4.time"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            main()
            assert MockIO.call_count == 2

    def test_main_sends_frame(self):
        from halucinator.external_devices.IEEE802_15_4 import main
        import binascii

        with mock.patch("sys.argv", [
            "ieee802", "-r", "5556", "5558", "-t", "5555", "5557",
        ]), mock.patch(
            "halucinator.external_devices.IEEE802_15_4.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ), mock.patch(
            "halucinator.external_devices.IEEE802_15_4.time"
        ), mock.patch(
            "builtins.input", side_effect=["AABB", KeyboardInterrupt]
        ):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            main()
