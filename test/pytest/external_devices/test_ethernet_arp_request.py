"""
Tests for halucinator.external_devices.ethernet_arp_request

Note: HostEthernetServer.__init__ creates a raw socket (AF_PACKET, SOCK_RAW)
that requires CAP_NET_RAW. Tests mock it to avoid needing root privileges.
"""

import sys
from unittest import mock

import pytest

# Install scapy mock permanently in sys.modules if scapy is not installed.
# Using mock.patch.dict would remove the module from sys.modules on exit,
# causing re-import issues with different module instances.
_need_scapy_mock = "scapy" not in sys.modules
if _need_scapy_mock:
    _scapy_mock = mock.MagicMock()
    sys.modules.setdefault("scapy", _scapy_mock)
    sys.modules.setdefault("scapy.all", _scapy_mock)

# Mock raw socket creation in host_ethernet_server to avoid CAP_NET_RAW requirement
with mock.patch(
    "halucinator.external_devices.host_ethernet_server.socket.socket"
), mock.patch(
    "halucinator.external_devices.host_ethernet_server.os"
):
    # Force import/reimport with mocks active
    if "halucinator.external_devices.ethernet_arp_request" in sys.modules:
        del sys.modules["halucinator.external_devices.ethernet_arp_request"]
    from halucinator.external_devices.ethernet_arp_request import ARPSender


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def arp_sender(mock_ioserver):
    return ARPSender(mock_ioserver)


class TestARPSenderInit:
    def test_stores_ioserver(self, mock_ioserver):
        sender = ARPSender(mock_ioserver)
        assert sender.ioserver is mock_ioserver

    def test_registers_tx_frame_topic(self, mock_ioserver):
        ARPSender(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.EthernetModel.tx_frame", mock.ANY
        )

    def test_no_host_eth_by_default(self, arp_sender):
        assert arp_sender.host_eth is None

    def test_with_host_interface(self, mock_ioserver):
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.HostEthernetServer"
        ) as MockHES:
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            sender = ARPSender(mock_ioserver, host_interface="eth0")
            assert sender.host_eth is mock_hes
            MockHES.assert_called_once_with("eth0", False)


class TestARPSenderSendRequest:
    def test_sends_arp_request(self, arp_sender, mock_ioserver):
        mock_ether = mock.Mock()
        mock_arp = mock.Mock()
        mock_ether.build.return_value = b"\x00\x01\x02"
        mock_scapy = mock.Mock()
        mock_scapy.Ether.return_value = mock_ether
        mock_scapy.ARP.return_value = mock_arp

        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.scapy", mock_scapy
        ):
            arp_sender.send_request("eth0")

            mock_scapy.Ether.assert_called_once()
            mock_scapy.ARP.assert_called_once()
            mock_ether.add_payload.assert_called_once_with(mock_arp)
            mock_ioserver.send_msg.assert_called_once_with(
                "Peripheral.EthernetModel.rx_frame",
                {"interface_id": "eth0", "frame": b"\x00\x01\x02"},
            )

    def test_sends_to_host_eth_if_present(self, mock_ioserver):
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.HostEthernetServer"
        ) as MockHES:
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            sender = ARPSender(mock_ioserver, host_interface="eth0")

            mock_ether = mock.Mock()
            mock_ether.build.return_value = b"\x00\x01"
            mock_scapy = mock.Mock()
            mock_scapy.Ether.return_value = mock_ether
            mock_scapy.ARP.return_value = mock.Mock()

            with mock.patch(
                "halucinator.external_devices.ethernet_arp_request.scapy", mock_scapy
            ):
                sender.send_request("eth0")
                mock_hes.send_msg.assert_called_once()


class TestARPSenderReceivedFrame:
    def test_received_frame_prints(self, arp_sender, mock_ioserver, capsys):
        mock_scapy = mock.Mock()
        mock_scapy.Ether.return_value = "parsed_frame"
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.scapy", mock_scapy
        ):
            msg = {"interface_id": "eth0", "frame": b"\x00\x01"}
            arp_sender.received_frame(mock_ioserver, msg)
            out = capsys.readouterr().out
            assert "eth0" in out or "parsed_frame" in out

    def test_received_frame_forwards_to_host(self, mock_ioserver):
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.HostEthernetServer"
        ) as MockHES:
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            sender = ARPSender(mock_ioserver, host_interface="eth0")
            mock_scapy = mock.Mock()
            mock_scapy.Ether.return_value = "parsed"
            with mock.patch(
                "halucinator.external_devices.ethernet_arp_request.scapy", mock_scapy
            ):
                msg = {"interface_id": "eth0", "frame": b"\x00"}
                sender.received_frame(mock_ioserver, msg)
                mock_hes.send_msg.assert_called_once_with(None, msg)


class TestARPSenderShutdown:
    def test_shutdown_no_host_eth(self, arp_sender):
        arp_sender.shutdown()  # Should not raise

    def test_shutdown_with_host_eth(self, mock_ioserver):
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.HostEthernetServer"
        ) as MockHES:
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes
            sender = ARPSender(mock_ioserver, host_interface="eth0")
            sender.shutdown()
            mock_hes.shutdown.assert_called_once()


class TestARPMainBlock:
    def test_main_block(self):
        """Test the __main__ block logic inline to avoid mock bypass via runpy."""
        with mock.patch(
            "halucinator.external_devices.ethernet_arp_request.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_arp_request.HostEthernetServer"
        ), mock.patch(
            "halucinator.external_devices.ethernet_arp_request.scapy"
        ), mock.patch(
            "halucinator.hal_log.setLogConfig"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            # Simulate __main__ block inline
            io_server = MockIO(5556, 5555)
            arp = ARPSender(io_server, host_interface=None)
            io_server.start()
            try:
                while True:
                    data = input("Press Enter to Send Arp Request")
                    arp.send_request("eth0")
            except KeyboardInterrupt:
                pass
            arp.shutdown()
            io_server.shutdown()
