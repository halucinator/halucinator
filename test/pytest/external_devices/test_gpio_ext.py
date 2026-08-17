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

            with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                gpio_mod.update_gpio(5555)

            mock_socket.connect.assert_called_once()

    def test_update_gpio_reads_with_input_not_raw_input(self):
        """update_gpio must use `input`, not Python 2's `raw_input`.

        This test used to assert the opposite -- that update_gpio raised
        NameError -- documenting the bug rather than the behaviour. `input` is
        mocked here rather than left to read the real stdin: under pytest's
        default capture, a genuine read raises

            OSError: pytest: reading from stdin while output is captured!

        so an unmocked prompt is not a way to prove anything about which
        builtin the source calls.
        """
        with mock.patch("halucinator.external_devices.gpio.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.gpio.time"):
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            ctx_instance.socket.return_value = mock.Mock()

            with mock.patch("builtins.input", side_effect=EOFError) as m_input:
                gpio_mod.update_gpio(5555)
            assert m_input.called, "update_gpio never prompted via input()"

    def test_update_gpio_publishes_with_send_string(self):
        """encode_zmq_msg returns str, and pyzmq's bytes-oriented send()
        rejects str with "unicode not allowed, use send_string" -- so the
        publish has to go through send_string or it raises on first use."""
        with mock.patch("halucinator.external_devices.gpio.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.gpio.time"):
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            # One pin/value pair, then end the loop.
            with mock.patch("builtins.input",
                            side_effect=["pin_id_0", "1", EOFError()]):
                gpio_mod.update_gpio(5555)

            mock_socket.send_string.assert_called_once()
            sent = mock_socket.send_string.call_args[0][0]
            assert isinstance(sent, str)
            assert "pin_id_0" in sent
            mock_socket.send.assert_not_called()
