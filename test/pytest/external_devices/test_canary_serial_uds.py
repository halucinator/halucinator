"""
Tests for halucinator.external_devices.canary_serial_uds

This module is essentially a main-block script that combines CanaryDevice and UDSTunnel.
We test that the imports work and the components can be instantiated.
"""

from unittest import mock

import pytest

from halucinator.external_devices.canary import CanaryDevice
from halucinator.external_devices.serial_uds import UDSTunnel


class TestCanarySerialUdsIntegration:
    """Test the components used by canary_serial_uds."""

    def test_canary_device_instantiation(self):
        mock_io = mock.Mock()
        cd = CanaryDevice(mock_io)
        assert cd.ioserver is mock_io
        mock_io.register_topic.assert_called_once()

    def test_uds_tunnel_instantiation(self):
        mock_io = mock.Mock()
        with mock.patch("halucinator.external_devices.serial_uds.socket.socket") as mock_sock:
            mock_sock_inst = mock.Mock()
            mock_sock.return_value = mock_sock_inst
            tunnel = UDSTunnel(mock_io, "/tmp/test.sock", "COM1")
            assert tunnel.ioserver is mock_io
            mock_sock_inst.connect.assert_called_once_with("/tmp/test.sock")

    def test_canary_and_uds_share_ioserver(self):
        mock_io = mock.Mock()
        with mock.patch("halucinator.external_devices.serial_uds.socket.socket") as mock_sock:
            mock_sock.return_value = mock.Mock()
            cd = CanaryDevice(mock_io)
            tunnel = UDSTunnel(mock_io, "/tmp/test.sock", "/utyCo/1")
            assert cd.ioserver is tunnel.ioserver

    def test_module_imports(self):
        """Verify the module can be imported."""
        import halucinator.external_devices.canary_serial_uds  # noqa: F401


class TestCanarySerialUdsMainBlock:
    def test_main_block_runs(self):
        import runpy

        with mock.patch(
            "halucinator.external_devices.ioserver.zmq.Context"
        ) as MockCtx, mock.patch(
            "halucinator.external_devices.serial_uds.socket.socket"
        ) as MockSock, mock.patch(
            "halucinator.hal_log.setLogConfig"
        ), mock.patch(
            "halucinator.external_devices.ioserver.IOServer.start"
        ), mock.patch(
            "halucinator.external_devices.ioserver.IOServer.shutdown"
        ):
            ctx_inst = mock.Mock()
            MockCtx.return_value = ctx_inst
            ctx_inst.socket.return_value = mock.Mock()
            MockSock.return_value = mock.Mock()

            with mock.patch.object(
                UDSTunnel, "recv_and_forward_uds_data",
                side_effect=KeyboardInterrupt,
            ), mock.patch("sys.argv", [
                "canary_serial_uds", "-a", "/tmp/test.sock",
            ]):
                runpy.run_module(
                    "halucinator.external_devices.canary_serial_uds",
                    run_name="__main__",
                )
