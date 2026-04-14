"""
Tests for halucinator.external_devices.gpio
"""

from unittest import mock

import pytest

import halucinator.external_devices.gpio as gpio_mod


class TestGpioMain:
    def test_main_prints_todo(self, capsys):
        with mock.patch("sys.argv", ["gpio"]):
            gpio_mod.main()
        captured = capsys.readouterr()
        assert "TODO" in captured.out


class TestGpioRxFromEmulator:
    def test_rx_connects_to_correct_port(self):
        with mock.patch("halucinator.external_devices.gpio.zmq.Context") as MockCtx:
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            mock_socket.recv_string.side_effect = Exception("break")

            with pytest.raises(Exception, match="break"):
                gpio_mod.rx_from_emulator(5556)
            mock_socket.connect.assert_called_once_with(
                "ipc:///tmp/Halucinator2IoServer5556"
            )


class TestGpioStart:
    def test_start_creates_process_but_has_known_bug(self):
        """gpio.start() chains Process(...).start() which returns None,
        then calls .join() on None. This is a known bug."""
        with mock.patch("halucinator.external_devices.gpio.Process") as MockProc, \
             mock.patch.object(gpio_mod, "update_gpio"):
            proc_instance = mock.Mock()
            proc_instance.start.return_value = None
            MockProc.return_value = proc_instance
            with pytest.raises(AttributeError):
                gpio_mod.start(None, 5556, 5555)
            MockProc.assert_called_once()


class TestGpioUpdateGpio:
    def test_update_gpio_connects_and_prompts(self):
        with mock.patch("halucinator.external_devices.gpio.zmq.Context") as MockCtx:
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            # raw_input raises NameError in Python 3 (it's a Python 2 function)
            # The source code uses raw_input which is a known bug
            with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                try:
                    gpio_mod.update_gpio(5555)
                except (KeyboardInterrupt, NameError):
                    pass

            mock_socket.connect.assert_called_once()

    def test_update_gpio_uses_raw_input(self):
        """update_gpio uses raw_input (Python 2), ensure NameError is raised."""
        with mock.patch("halucinator.external_devices.gpio.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.gpio.time"):
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            ctx_instance.socket.return_value = mock.Mock()

            # In Python 3, raw_input doesn't exist, so this raises NameError
            with pytest.raises(NameError):
                gpio_mod.update_gpio(5555)
