"""
Tests for halucinator.external_devices.serial_uds
"""

from argparse import ArgumentParser
from unittest import mock

import pytest

from halucinator.external_devices.serial_uds import UDSTunnel


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def mock_socket():
    with mock.patch("halucinator.external_devices.serial_uds.socket.socket") as MockSock:
        mock_inst = mock.Mock()
        MockSock.return_value = mock_inst
        yield MockSock, mock_inst


class TestUDSTunnelInit:
    def test_stores_ioserver(self, mock_ioserver, mock_socket):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        assert tunnel.ioserver is mock_ioserver

    def test_stores_tty_model_id(self, mock_ioserver, mock_socket):
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        assert tunnel.tty_model_id == "COM1"

    def test_connects_to_socket(self, mock_ioserver, mock_socket):
        MockSock, mock_inst = mock_socket
        UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        import socket
        MockSock.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        mock_inst.connect.assert_called_once_with("/tmp/test.sock")

    def test_registers_topic(self, mock_ioserver, mock_socket):
        UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.UTTYModel.tx_buf", mock.ANY
        )

    def test_prev_print_none(self, mock_ioserver, mock_socket):
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        assert tunnel.prev_print is None


class TestUDSTunnelWriteHandler:
    def test_sends_bytes_to_socket(self, mock_ioserver, mock_socket):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        msg = {"chars": b"hello"}
        tunnel.write_handler(mock_ioserver, msg)
        mock_inst.send.assert_called_once_with(b"hello")


class TestUDSTunnelSendData:
    def test_sends_correct_message(self, mock_ioserver, mock_socket):
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        tunnel.send_data("COM1", [0x41])
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": "COM1", "char": [0x41]},
        )


class TestUDSTunnelRecvAndForward:
    def test_recv_and_forward_with_data(self, mock_ioserver, mock_socket, capsys):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        mock_inst.recv.return_value = b"\x41"
        tunnel.recv_and_forward_uds_data(1)
        mock_ioserver.send_msg.assert_called_once()
        out = capsys.readouterr().out
        assert "From VM" in out

    def test_recv_and_forward_empty(self, mock_ioserver, mock_socket):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        mock_inst.recv.return_value = b""
        tunnel.recv_and_forward_uds_data(1)
        mock_ioserver.send_msg.assert_not_called()

    def test_recv_custom_bytes(self, mock_ioserver, mock_socket):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        mock_inst.recv.return_value = b"\x41\x42"
        tunnel.recv_and_forward_uds_data(2)
        mock_inst.recv.assert_called_once_with(2)


class TestUDSTunnelShutdown:
    def test_closes_socket(self, mock_ioserver, mock_socket):
        _, mock_inst = mock_socket
        tunnel = UDSTunnel(mock_ioserver, "/tmp/test.sock", "COM1")
        tunnel.shutdown()
        mock_inst.close.assert_called_once()


class TestUDSTunnelAddArgs:
    def test_adds_required_args(self):
        parser = ArgumentParser()
        UDSTunnel.add_args(parser)
        # -a is required, so test with it
        args = parser.parse_args(["-a", "/tmp/test.sock"])
        assert args.addr == "/tmp/test.sock"
        assert args.tty_id == "COM1"

    def test_custom_tty_id(self):
        parser = ArgumentParser()
        UDSTunnel.add_args(parser)
        args = parser.parse_args(["-a", "/tmp/test.sock", "--tty_id", "COM2"])
        assert args.tty_id == "COM2"


class TestSerialUdsMainBlock:
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
            mock_sock_inst = mock.Mock()
            mock_sock_inst.recv.side_effect = KeyboardInterrupt
            MockSock.return_value = mock_sock_inst

            with mock.patch("sys.argv", [
                "serial_uds", "-a", "/tmp/test.sock",
            ]):
                runpy.run_module(
                    "halucinator.external_devices.serial_uds",
                    run_name="__main__",
                )
