"""
Tests for halucinator.external_devices.ethernet_wireless
"""

from unittest import mock

import pytest

from halucinator.external_devices.ethernet_wireless import main


class TestEthernetWirelessMain:
    def test_port_mismatch_exits(self, capsys):
        with mock.patch(
            "sys.argv",
            ["ethernet_wireless", "-r", "5556", "5558", "-t", "5555"],
        ):
            with pytest.raises(SystemExit):
                main()

    def test_main_creates_hubs(self):
        with mock.patch("sys.argv", [
            "ethernet_wireless", "-r", "5556", "5558", "-t", "5555", "5557",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_wireless.ViruatalEthHub"
        ) as MockEthHub, mock.patch(
            "halucinator.external_devices.ethernet_wireless.IEEE802_15_4"
        ) as MockWireless, mock.patch(
            "halucinator.external_devices.ethernet_wireless.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.hal_log"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_eth_hub = mock.Mock()
            MockEthHub.return_value = mock_eth_hub
            mock_wireless = mock.Mock()
            MockWireless.return_value = mock_wireless
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst

            main()

            MockEthHub.assert_called_once()
            MockWireless.assert_called_once()
            # Two servers created for two port pairs
            assert MockIO.call_count == 2
            mock_eth_hub.shutdown.assert_called_once()
            mock_wireless.shutdown.assert_called_once()

    def test_main_with_interface(self):
        with mock.patch("sys.argv", [
            "ethernet_wireless", "-r", "5556", "5558", "-t", "5555", "5557",
            "-i", "eth0",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_wireless.ViruatalEthHub"
        ) as MockEthHub, mock.patch(
            "halucinator.external_devices.ethernet_wireless.IEEE802_15_4"
        ) as MockWireless, mock.patch(
            "halucinator.external_devices.ethernet_wireless.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_wireless.HostEthernetServer"
        ) as MockHES, mock.patch(
            "halucinator.hal_log"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_eth_hub = mock.Mock()
            MockEthHub.return_value = mock_eth_hub
            mock_wireless = mock.Mock()
            MockWireless.return_value = mock_wireless
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            mock_hes = mock.Mock()
            MockHES.return_value = mock_hes

            main()

            MockHES.assert_called_once_with("eth0", False)
            mock_eth_hub.add_server.assert_any_call(mock_hes)
            mock_hes.start.assert_called_once()

    def test_main_with_listen_to_host(self):
        with mock.patch("sys.argv", [
            "ethernet_wireless", "-r", "5556", "5558", "-t", "5555", "5557",
            "-i", "eth0", "-l",
        ]), mock.patch(
            "halucinator.external_devices.ethernet_wireless.ViruatalEthHub"
        ) as MockEthHub, mock.patch(
            "halucinator.external_devices.ethernet_wireless.IEEE802_15_4"
        ), mock.patch(
            "halucinator.external_devices.ethernet_wireless.IOServer"
        ) as MockIO, mock.patch(
            "halucinator.external_devices.ethernet_wireless.HostEthernetServer"
        ) as MockHES, mock.patch(
            "halucinator.hal_log"
        ), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            MockEthHub.return_value = mock.Mock()
            MockIO.return_value = mock.Mock()
            MockHES.return_value = mock.Mock()

            main()

            MockHES.assert_called_once_with("eth0", True)
