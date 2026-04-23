"""
Tests for halucinator.external_devices.adc
"""

from unittest import mock

import pytest

import halucinator.external_devices.adc as adc_mod


class TestAdcRxFromEmulator:
    def test_rx_connects_to_correct_port(self):
        """Test that rx_from_emulator connects to the right IPC pipe."""
        with mock.patch("halucinator.external_devices.adc.zmq.Context") as MockCtx:
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            # Make recv_string raise to break the loop
            mock_socket.recv_string.side_effect = Exception("break")

            with pytest.raises(Exception, match="break"):
                adc_mod.rx_from_emulator(5556)
            mock_socket.connect.assert_called_once_with(
                "ipc:///tmp/Halucinator2IoServer5556"
            )
            mock_socket.setsockopt_string.assert_called()


class TestAdcUpdateAdc:
    def test_update_adc_connects_and_prompts(self):
        """Test that update_adc connects to the correct port."""
        with mock.patch("halucinator.external_devices.adc.zmq.Context") as MockCtx:
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            # Simulate KeyboardInterrupt on first input to exit loop
            with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                adc_mod.update_adc(5555)

            mock_socket.bind.assert_called_once_with("tcp://*:5555")


class TestAdcStart:
    def test_start_calls_bug_marker(self):
        """Test that start() calls the BUG marker."""
        with mock.patch.object(adc_mod, "markers") as mock_markers, \
             mock.patch("halucinator.external_devices.adc.Process") as MockProc, \
             mock.patch.object(adc_mod, "update_adc"):
            proc_instance = mock.Mock()
            MockProc.return_value = proc_instance

            adc_mod.start(5556, 5555)
            mock_markers.BUG.assert_called_once()


class TestAdcMain:
    def test_main_parses_args(self):
        """Test that main() runs without error with default args."""
        with mock.patch.object(adc_mod, "start") as mock_start, \
             mock.patch("sys.argv", ["adc"]):
            adc_mod.main()
            mock_start.assert_called_once()

    def test_main_custom_ports(self):
        with mock.patch.object(adc_mod, "start") as mock_start, \
             mock.patch("sys.argv", ["adc", "-r", "7777", "-t", "8888"]):
            adc_mod.main()
            mock_start.assert_called_once_with("7777", "8888")


class TestAdcRxFromEmulatorLoop:
    def test_rx_processes_message(self):
        with mock.patch("halucinator.external_devices.adc.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.adc.decode_zmq_msg") as mock_decode:
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket

            # Use module vars to control the loop
            mv = vars(adc_mod)
            old_run = mv["__run_server"]

            call_count = [0]
            def recv_effect():
                call_count[0] += 1
                if call_count[0] > 1:
                    mv["__run_server"] = False
                    return "raw_msg"
                return "raw_msg"

            mock_socket.recv_string.side_effect = recv_effect
            mock_decode.return_value = ("Peripheral.ADC.adc_write", {"adc_id": "adc0", "value": 42})

            try:
                mv["__run_server"] = True
                adc_mod.rx_from_emulator(5556)
                assert call_count[0] >= 1
            finally:
                mv["__run_server"] = old_run


class TestAdcUpdateAdcLoop:
    def test_update_sends_message(self):
        with mock.patch("halucinator.external_devices.adc.zmq.Context") as MockCtx, \
             mock.patch("halucinator.external_devices.adc.encode_zmq_msg") as mock_encode, \
             mock.patch("halucinator.external_devices.adc.time"):
            ctx_instance = mock.Mock()
            MockCtx.return_value = ctx_instance
            mock_socket = mock.Mock()
            ctx_instance.socket.return_value = mock_socket
            mock_encode.return_value = "encoded"

            inputs = iter(["adc0", "42", KeyboardInterrupt])
            with mock.patch("builtins.input", side_effect=inputs):
                adc_mod.update_adc(5555)
            mock_socket.send_string.assert_called_once_with("encoded")
