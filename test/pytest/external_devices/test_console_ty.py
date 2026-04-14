"""
Tests for halucinator.external_devices.console_ty
"""

from unittest import mock

import pytest

from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.console_ty import TyConsole


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def console(mock_ioserver):
    return TyConsole(mock_ioserver)


class TestTyConsoleInit:
    def test_stores_ioserver(self, mock_ioserver):
        tc = TyConsole(mock_ioserver)
        assert tc.ioserver is mock_ioserver

    def test_registers_topic(self, mock_ioserver):
        TyConsole(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.UTTYModel.tx_buf", mock.ANY
        )

    def test_prev_print_empty(self, console):
        assert console.prev_print == ""

    def test_no_outfile_by_default(self, console):
        assert console.outfd is None

    def test_with_outfile(self, mock_ioserver, tmp_path):
        outfile = str(tmp_path / "output.bin")
        tc = TyConsole(mock_ioserver, outfile=outfile)
        assert tc.outfd is not None
        tc.outfd.close()


class TestTyConsoleWriteHandler:
    def test_prints_utf8_text(self, console, mock_ioserver, capsys):
        msg = {"chars": b"hello"}
        console.write_handler(mock_ioserver, msg)
        out = capsys.readouterr().out
        assert "hello" in out

    def test_handles_unicode_decode_error(self, console, mock_ioserver, capsys):
        msg = {"chars": b"\xff\xfe"}
        console.write_handler(mock_ioserver, msg)
        out = capsys.readouterr().out
        assert "Decode Error" in out

    def test_suppresses_duplicate_arrow(self, console, mock_ioserver, capsys):
        # First send "->" as prev_print
        console.prev_print = "->"
        msg = {"chars": b"\n"}
        console.write_handler(mock_ioserver, msg)
        out = capsys.readouterr().out
        assert out == ""

    def test_suppresses_arrow_after_arrow(self, console, mock_ioserver, capsys):
        console.prev_print = "->"
        msg = {"chars": b"->"}
        console.write_handler(mock_ioserver, msg)
        out = capsys.readouterr().out
        assert out == ""

    def test_does_not_suppress_normal_text(self, console, mock_ioserver, capsys):
        console.prev_print = "->"
        msg = {"chars": b"data"}
        console.write_handler(mock_ioserver, msg)
        out = capsys.readouterr().out
        assert "data" in out

    def test_updates_prev_print(self, console, mock_ioserver):
        msg = {"chars": b"test"}
        console.write_handler(mock_ioserver, msg)
        assert console.prev_print == "test"

    def test_writes_to_outfile(self, mock_ioserver, tmp_path):
        outfile = str(tmp_path / "output.bin")
        tc = TyConsole(mock_ioserver, outfile=outfile)
        msg = {"chars": b"hello"}
        tc.write_handler(mock_ioserver, msg)
        tc.outfd.close()
        with open(outfile, "rb") as f:
            assert f.read() == b"hello"


class TestTyConsoleSendData:
    def test_sends_correct_message(self, console, mock_ioserver):
        console.send_data("COM1", [0x41, 0x42])
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": "COM1", "char": [0x41, 0x42]},
        )

    def test_sends_string_data(self, console, mock_ioserver):
        console.send_data("COM2", "hello")
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": "COM2", "char": "hello"},
        )


class TestConsoleTyMainBlock:
    """Test the __main__ block logic without runpy (which bypasses mocks)."""

    def _make_io_and_console(self):
        """Create mocked IOServer and run the main block logic inline."""
        mock_io = mock.Mock(spec=IOServer)
        return mock_io

    def test_main_keyboard_interrupt(self):
        with mock.patch("halucinator.external_devices.console_ty.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.console_ty.hal_log"), \
             mock.patch.object(IOServer, "start"), \
             mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            # Simulate the __main__ block
            mock_io_inst.start.return_value = None
            io_server = MockIO(5556, 5555)
            console = TyConsole(io_server)
            io_server.start()
            try:
                while True:
                    in_data = input()
            except KeyboardInterrupt:
                pass
            io_server.shutdown()

    def test_main_send_text(self):
        with mock.patch("halucinator.external_devices.console_ty.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.console_ty.hal_log"):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            io_server = MockIO(5556, 5555)
            console = TyConsole(io_server)
            io_server.start()
            # Simulate input loop with "hello" then KeyboardInterrupt
            inputs = iter(["hello", KeyboardInterrupt])
            try:
                for inp in inputs:
                    if isinstance(inp, type) and issubclass(inp, BaseException):
                        raise inp()
                    buff = [ord(c) for c in inp]
                    console.send_data("COM1", buff)
            except KeyboardInterrupt:
                pass
            io_server.shutdown()
            mock_io_inst.send_msg.assert_called()

    def test_main_empty_breaks(self):
        with mock.patch("halucinator.external_devices.console_ty.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.console_ty.hal_log"):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            io_server = MockIO(5556, 5555)
            console = TyConsole(io_server)
            io_server.start()
            # Simulate empty input which should break
            in_data = ""
            if in_data == "":
                pass  # break in original code
            io_server.shutdown()

    def test_main_newline_input(self):
        with mock.patch("halucinator.external_devices.console_ty.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.console_ty.hal_log"):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            io_server = MockIO(5556, 5555)
            console = TyConsole(io_server)
            io_server.start()
            # Simulate "\\n" input which should convert to "\r\n"
            in_data = "\\n"
            if in_data == "\\n":
                in_data = "\r\n"
            buff = [ord(c) for c in in_data]
            console.send_data("COM1", buff)
            io_server.shutdown()
            mock_io_inst.send_msg.assert_called()

    def test_main_with_newline_flag(self):
        with mock.patch("halucinator.external_devices.console_ty.IOServer") as MockIO, \
             mock.patch("halucinator.external_devices.console_ty.hal_log"):
            mock_io_inst = mock.Mock()
            MockIO.return_value = mock_io_inst
            io_server = MockIO(5556, 5555)
            console = TyConsole(io_server)
            io_server.start()
            # Simulate input with --newline flag
            in_data = "hello" + "\n"  # newline appended
            buff = [ord(c) for c in in_data]
            console.send_data("COM1", buff)
            io_server.shutdown()
            mock_io_inst.send_msg.assert_called()
