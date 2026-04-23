"""
Tests for halucinator.external_devices.uart - UARTPrintServer class
"""

from unittest import mock

import pytest

from halucinator.external_devices.uart import UARTPrintServer


@pytest.fixture
def mock_ioserver():
    """Create a mock IOServer."""
    ioserver = mock.Mock()
    return ioserver


@pytest.fixture
def uart_server(mock_ioserver):
    """Create a UARTPrintServer with a mocked IOServer."""
    return UARTPrintServer(mock_ioserver)


class TestUARTPrintServerInit:
    def test_registers_write_topic(self, mock_ioserver):
        server = UARTPrintServer(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            'Peripheral.UARTPublisher.write', server.write_handler
        )
        assert server.ioserver is mock_ioserver
        assert server.prev_print is None


class TestWriteHandler:
    def test_prints_text(self, uart_server, mock_ioserver, capsys):
        msg = {'chars': b'Hello World'}
        uart_server.write_handler(mock_ioserver, msg)
        captured = capsys.readouterr()
        assert 'Hello World' in captured.out

    def test_suppresses_duplicate_arrow_prompt(self, uart_server, mock_ioserver, capsys):
        msg = {'chars': b'-> '}
        # First call should print
        uart_server.write_handler(mock_ioserver, msg)
        captured = capsys.readouterr()
        assert '-> ' in captured.out

        # Second consecutive "-> " should be suppressed
        uart_server.write_handler(mock_ioserver, msg)
        captured = capsys.readouterr()
        assert captured.out == ''

    def test_different_text_after_arrow_prints(self, uart_server, mock_ioserver, capsys):
        msg1 = {'chars': b'-> '}
        msg2 = {'chars': b'data'}
        uart_server.write_handler(mock_ioserver, msg1)
        capsys.readouterr()

        uart_server.write_handler(mock_ioserver, msg2)
        captured = capsys.readouterr()
        assert 'data' in captured.out

    def test_decodes_latin1(self, uart_server, mock_ioserver, capsys):
        # Latin-1 encoded byte
        msg = {'chars': bytes([0xE9])}  # e with accent
        uart_server.write_handler(mock_ioserver, msg)
        captured = capsys.readouterr()
        assert '\xe9' in captured.out


class TestSendData:
    def test_sends_rx_data_message(self, uart_server, mock_ioserver):
        uart_server.send_data(0x1234, "test data")
        mock_ioserver.send_msg.assert_called_once_with(
            'Peripheral.UARTPublisher.rx_data',
            {'id': 0x1234, 'chars': 'test data'}
        )


class TestUartMain:
    def test_main_sends_data_then_exits(self):
        with mock.patch("halucinator.external_devices.uart.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["uart", "-r", "5556", "-t", "5555", "-i", "100"]), \
             mock.patch("builtins.input", side_effect=["hello", ""]):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.uart import main
            main()
            mock_io.start.assert_called_once()
            mock_io.shutdown.assert_called_once()

    def test_main_newline_flag(self):
        # With --newline, "data\n" != "" so we need KeyboardInterrupt to exit
        with mock.patch("halucinator.external_devices.uart.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["uart", "-r", "5556", "-t", "5555", "-n"]), \
             mock.patch("builtins.input", side_effect=["data", KeyboardInterrupt]):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.uart import main
            main()

    def test_main_backslash_n_input(self):
        with mock.patch("halucinator.external_devices.uart.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["uart", "-r", "5556", "-t", "5555"]), \
             mock.patch("builtins.input", side_effect=["\\n", ""]):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.uart import main
            main()

    def test_main_keyboard_interrupt(self):
        with mock.patch("halucinator.external_devices.uart.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["uart", "-r", "5556", "-t", "5555"]), \
             mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.uart import main
            main()
            mock_io.shutdown.assert_called_once()
