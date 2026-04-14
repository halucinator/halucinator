"""
Tests for halucinator.external_devices.ethernet_virt_hub
"""

from unittest import mock

import pytest

from halucinator.external_devices.ethernet_virt_hub import (
    VirtualEthHub,
    ViruatalEthHub,
    main,
)


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def hub():
    return VirtualEthHub()


class TestVirtualEthHubInit:
    def test_empty_init(self):
        hub = VirtualEthHub()
        assert hub.ioservers == []
        assert hub.host_socket is None
        assert hub.host_interface is None

    def test_init_with_servers(self):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub = VirtualEthHub(ioservers=[s1, s2])
        assert len(hub.ioservers) == 2


class TestVirtualEthHubAddServer:
    def test_adds_server(self, hub, mock_ioserver):
        hub.add_server(mock_ioserver)
        assert mock_ioserver in hub.ioservers

    def test_registers_topic(self, hub, mock_ioserver):
        hub.add_server(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.EthernetModel.tx_frame", hub.received_frame
        )


class TestVirtualEthHubReceivedFrame:
    def test_forwards_to_other_servers(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)
        msg = {"interface_id": "eth0", "frame": b"\x01\x02"}
        hub.received_frame(s1, msg)
        s2.send_msg.assert_called_once_with(
            "Peripheral.EthernetModel.rx_frame", msg
        )
        s1.send_msg.assert_not_called()

    def test_forwards_from_none_to_all(self, hub):
        s1 = mock.Mock()
        s2 = mock.Mock()
        hub.add_server(s1)
        hub.add_server(s2)
        msg = {"interface_id": "eth0", "frame": b"\x01"}
        hub.received_frame(None, msg)
        s1.send_msg.assert_called_once()
        s2.send_msg.assert_called_once()

    def test_single_server_no_forward_to_self(self, hub):
        s1 = mock.Mock()
        hub.add_server(s1)
        msg = {"interface_id": "eth0", "frame": b"\x01"}
        hub.received_frame(s1, msg)
        s1.send_msg.assert_not_called()


class TestVirtualEthHubShutdown:
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


class TestBackwardsAlias:
    def test_viruatal_alias(self):
        assert ViruatalEthHub is VirtualEthHub


class TestVirtualEthHubMain:
    def test_port_mismatch_exits(self, capsys):
        with mock.patch("sys.argv", [
            "ethernet_virt_hub", "-r", "5556", "5558", "-t", "5555",
        ]):
            with pytest.raises(SystemExit):
                main()

    def test_main_creates_hub(self):
        with mock.patch("sys.argv", [
            "ethernet_virt_hub", "-r", "5556", "5558", "-t", "5555", "5557",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.HostEthernetServer"
        ), mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.SendInterrupt"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt), \
             mock.patch("halucinator.external_devices.ethernet_virt_hub.time"):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            main()
            assert MockIO.call_count == 2

    def test_main_with_interface(self):
        with mock.patch("sys.argv", [
            "ethernet_virt_hub", "-r", "5556", "5558", "-t", "5555", "5557",
            "-i", "eth0",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.HostEthernetServer"
        ) as MockHES, mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.SendInterrupt"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt), \
             mock.patch("halucinator.external_devices.ethernet_virt_hub.time"):
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            MockIO.return_value = mock.Mock()

            main()
            MockHES.assert_called_once_with("eth0", False)
            mock_hes.start.assert_called_once()

    def test_main_with_enable_host_rx(self):
        with mock.patch("sys.argv", [
            "ethernet_virt_hub", "-r", "5556", "5558", "-t", "5555", "5557",
            "-i", "eth0", "-p",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.HostEthernetServer"
        ) as MockHES, mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_virt_hub.SendInterrupt"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt), \
             mock.patch("halucinator.external_devices.ethernet_virt_hub.time"):
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            MockIO.return_value = mock.Mock()

            main()
            MockHES.assert_called_once_with("eth0", True)
