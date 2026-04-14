"""
Tests for halucinator.external_devices.ioserver - IOServer class
"""

from argparse import ArgumentParser
from unittest import mock

import pytest
import zmq

from halucinator.external_devices.ioserver import IOServer


@pytest.fixture
def mock_zmq_context():
    """Mock zmq.Context and zmq.Poller to prevent real socket connections and exit hangs."""
    with mock.patch("halucinator.external_devices.ioserver.zmq.Context") as MockCtx, \
         mock.patch("halucinator.external_devices.ioserver.zmq.Poller"):
        ctx_instance = mock.Mock()
        MockCtx.return_value = ctx_instance
        mock_socket = mock.Mock()
        ctx_instance.socket.return_value = mock_socket
        yield ctx_instance, mock_socket


class TestIOServerInit:
    def test_default_ports(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer()
        # Should connect to default ports
        mock_socket.connect.assert_any_call("ipc:///tmp/Halucinator2IoServer5556")
        mock_socket.connect.assert_any_call("ipc:///tmp/IoServer2Halucinator5555")
        assert server.handlers == {}
        assert server.packet_log is None

    def test_custom_ports(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer(rx_port=7777, tx_port=8888)
        mock_socket.connect.assert_any_call("ipc:///tmp/Halucinator2IoServer7777")
        mock_socket.connect.assert_any_call("ipc:///tmp/IoServer2Halucinator8888")

    def test_parser_args_override(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        args = mock.Mock()
        args.rx_port = 9999
        args.tx_port = 1111
        server = IOServer(parser_args=args)
        mock_socket.connect.assert_any_call("ipc:///tmp/Halucinator2IoServer9999")
        mock_socket.connect.assert_any_call("ipc:///tmp/IoServer2Halucinator1111")

    def test_with_log_file(self, mock_zmq_context, tmp_path):
        ctx_instance, mock_socket = mock_zmq_context
        log_file = str(tmp_path / "test.log")
        server = IOServer(log_file=log_file)
        assert server.packet_log is not None
        server.packet_log.close()


class TestIOServerRegisterTopic:
    def test_registers_topic_and_handler(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer()

        handler = mock.Mock()
        server.register_topic("TestTopic", handler)

        assert "TestTopic" in server.handlers
        assert server.handlers["TestTopic"] is handler
        mock_socket.setsockopt.assert_called_with(
            zmq.SUBSCRIBE, b"TestTopic"
        )


class TestIOServerSendMsg:
    def test_sends_encoded_message(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer()

        with mock.patch("halucinator.external_devices.ioserver.encode_zmq_msg") as mock_encode:
            mock_encode.return_value = "encoded_msg"
            server.send_msg("topic", {"key": "val"})
            mock_encode.assert_called_once_with("topic", {"key": "val"})
            mock_socket.send_string.assert_called_once_with("encoded_msg")

    def test_sends_msg_with_log_and_frame(self, mock_zmq_context, tmp_path):
        ctx_instance, mock_socket = mock_zmq_context
        log_file = str(tmp_path / "test.log")
        server = IOServer(log_file=log_file)

        with mock.patch("halucinator.external_devices.ioserver.encode_zmq_msg") as mock_encode:
            mock_encode.return_value = "encoded_msg"
            server.send_msg("topic", {"frame": b"\x01\x02"})
            mock_socket.send_string.assert_called_once_with("encoded_msg")

        server.packet_log.close()

    def test_sends_msg_with_log_no_frame(self, mock_zmq_context, tmp_path):
        ctx_instance, mock_socket = mock_zmq_context
        log_file = str(tmp_path / "test.log")
        server = IOServer(log_file=log_file)

        with mock.patch("halucinator.external_devices.ioserver.encode_zmq_msg") as mock_encode:
            mock_encode.return_value = "encoded_msg"
            server.send_msg("topic", {"key": "val"})
            mock_socket.send_string.assert_called_once_with("encoded_msg")

        server.packet_log.close()


class TestIOServerShutdown:
    def test_shutdown_sets_stop(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer()
        server.shutdown()
        # Internal __stop should be set
        assert server._IOServer__stop.is_set()

    def test_shutdown_closes_log(self, mock_zmq_context, tmp_path):
        ctx_instance, mock_socket = mock_zmq_context
        log_file = str(tmp_path / "test.log")
        server = IOServer(log_file=log_file)
        server.shutdown()
        assert server.packet_log.closed


class TestIOServerRun:
    def test_run_processes_messages(self, mock_zmq_context):
        ctx_instance, mock_socket = mock_zmq_context
        server = IOServer()

        handler = mock.Mock()
        server.register_topic("TestTopic", handler)

        # Make poller return data once then stop
        call_count = [0]
        def poll_side_effect(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                return {server.rx_socket: zmq.POLLIN}
            server.shutdown()
            return {}

        server.poller = mock.Mock()
        server.poller.poll = mock.Mock(side_effect=poll_side_effect)

        with mock.patch("halucinator.external_devices.ioserver.decode_zmq_msg") as mock_decode:
            mock_decode.return_value = ("TestTopic", {"frame": b"data"})
            server.rx_socket.recv_string.return_value = "raw_msg"
            server.run()

        handler.assert_called_once_with(server, {"frame": b"data"})

    def test_run_with_logging(self, mock_zmq_context, tmp_path):
        ctx_instance, mock_socket = mock_zmq_context
        log_file = str(tmp_path / "test.log")
        server = IOServer(log_file=log_file)

        handler = mock.Mock()
        server.register_topic("TestTopic", handler)

        call_count = [0]
        def poll_side_effect(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                return {server.rx_socket: zmq.POLLIN}
            server.shutdown()
            return {}

        server.poller = mock.Mock()
        server.poller.poll = mock.Mock(side_effect=poll_side_effect)

        with mock.patch("halucinator.external_devices.ioserver.decode_zmq_msg") as mock_decode:
            mock_decode.return_value = ("TestTopic", {"frame": b"data"})
            server.rx_socket.recv_string.return_value = "raw_msg"
            server.run()

        # Log file should not be closed yet (shutdown closes it)
        server.packet_log.close()


class TestIOServerAddArgs:
    def test_adds_rx_and_tx_args(self):
        parser = ArgumentParser()
        IOServer.add_args(parser)
        args = parser.parse_args([])
        assert args.rx_port == 5556
        assert args.tx_port == 5555

    def test_custom_args(self):
        parser = ArgumentParser()
        IOServer.add_args(parser)
        args = parser.parse_args(["-r", "7777", "-t", "8888"])
        assert args.rx_port == "7777"
        assert args.tx_port == "8888"


class TestIOServerMain:
    def test_main_keyboard_interrupt(self):
        from halucinator.external_devices.ioserver import main as io_main

        with mock.patch("halucinator.external_devices.ioserver.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.ioserver.zmq.Poller") as MockPoller, \
             mock.patch("halucinator.external_devices.ioserver.hal_log"), \
             mock.patch.object(IOServer, "start"), \
             mock.patch("sys.argv", ["ioserver"]), \
             mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            ctx_inst = mock.Mock()
            MockCtx.return_value = ctx_inst
            ctx_inst.socket.return_value = mock.Mock()
            io_main()

    def test_main_sends_one_msg(self):
        from halucinator.external_devices.ioserver import main as io_main

        with mock.patch("halucinator.external_devices.ioserver.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.ioserver.zmq.Poller") as MockPoller, \
             mock.patch("halucinator.external_devices.ioserver.hal_log"), \
             mock.patch.object(IOServer, "start"), \
             mock.patch("sys.argv", ["ioserver"]):
            ctx_inst = mock.Mock()
            MockCtx.return_value = ctx_inst
            mock_socket = mock.Mock()
            ctx_inst.socket.return_value = mock_socket

            inputs = iter(["MyTopic", "id1", "data1", KeyboardInterrupt])
            with mock.patch("builtins.input", side_effect=inputs), \
                 mock.patch("halucinator.external_devices.ioserver.encode_zmq_msg") as mock_encode:
                mock_encode.return_value = "encoded"
                io_main()
            mock_socket.send_string.assert_called()
