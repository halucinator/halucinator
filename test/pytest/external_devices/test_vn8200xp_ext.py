"""
Tests for halucinator.external_devices.vn8200xp - VN8200XP class
"""

from unittest import mock

import pytest

from halucinator.external_devices.vn8200xp import VN8200XP


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


class TestVN8200XPInit:
    def test_registers_write_topic(self, mock_ioserver):
        vn = VN8200XP(mock_ioserver)
        mock_ioserver.register_topic.assert_called_once_with(
            "Peripheral.UARTPublisher.write", vn.write_handler
        )
        assert vn.ioserver is mock_ioserver


class TestVN8200XPWriteHandler:
    def test_write_handler_prints_and_embeds(self, mock_ioserver, capsys):
        vn = VN8200XP(mock_ioserver)
        msg = {"data": "test"}

        with mock.patch("halucinator.external_devices.vn8200xp.IPython") as mock_ipython:
            vn.write_handler(mock_ioserver, msg)
            captured = capsys.readouterr()
            assert "test" in captured.out
            mock_ipython.embed.assert_called_once()


class TestVN8200XPSendData:
    def test_sends_rx_data_message(self, mock_ioserver):
        vn = VN8200XP(mock_ioserver)
        vn.send_data(0x1234, "test chars")
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.UARTPublisher.rx_data",
            {"id": 0x1234, "chars": "test chars"}
        )


class TestVN8200XPMain:
    def test_main_sends_data_then_exits(self):
        with mock.patch("halucinator.external_devices.vn8200xp.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["vn8200xp", "-r", "5556", "-t", "5555"]), \
             mock.patch("builtins.input", side_effect=["hello", ""]):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.vn8200xp import main
            main()
            mock_io.start.assert_called_once()
            mock_io.shutdown.assert_called_once()

    def test_main_backslash_n_converted(self):
        with mock.patch("halucinator.external_devices.vn8200xp.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["vn8200xp", "-r", "5556", "-t", "5555"]), \
             mock.patch("builtins.input", side_effect=["\\n", ""]):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.vn8200xp import main
            main()

    def test_main_keyboard_interrupt(self):
        with mock.patch("halucinator.external_devices.vn8200xp.IOServer") as MockIO, \
             mock.patch("halucinator.hal_log.setLogConfig"), \
             mock.patch("sys.argv", ["vn8200xp", "-r", "5556", "-t", "5555"]), \
             mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            mock_io = mock.Mock()
            MockIO.return_value = mock_io
            from halucinator.external_devices.vn8200xp import main
            main()
            mock_io.shutdown.assert_called_once()
