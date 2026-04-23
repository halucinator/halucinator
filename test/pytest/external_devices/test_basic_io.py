"""
Tests for halucinator.external_devices.basic_io
"""

from unittest import mock

import pytest

from halucinator.external_devices.basic_io import BasicIO


@pytest.fixture
def mock_ioserver():
    return mock.Mock()


@pytest.fixture
def basic_io(mock_ioserver):
    return BasicIO(mock_ioserver)


class TestBasicIOInit:
    def test_stores_ioserver(self, mock_ioserver):
        bio = BasicIO(mock_ioserver)
        assert bio.ioserver is mock_ioserver

    def test_empty_dicts(self, basic_io):
        assert basic_io.analog_values == {}
        assert basic_io.digital_values == {}

    def test_registers_digital_topic(self, mock_ioserver):
        BasicIO(mock_ioserver)
        calls = mock_ioserver.register_topic.call_args_list
        topics = [c[0][0] for c in calls]
        assert "Peripheral.DigitalIOModel.internal_update" in topics

    def test_registers_analog_topic(self, mock_ioserver):
        BasicIO(mock_ioserver)
        calls = mock_ioserver.register_topic.call_args_list
        topics = [c[0][0] for c in calls]
        assert "Peripheral.AnalogIOModel.internal_update" in topics


class TestDigitalWriteHandler:
    def test_stores_digital_value(self, basic_io, mock_ioserver):
        msg = {"id": "pin1", "value": 1}
        basic_io.digital_write_handler(mock_ioserver, msg)
        assert basic_io.digital_values["pin1"] == 1

    def test_overwrites_digital_value(self, basic_io, mock_ioserver):
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin1", "value": 0})
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin1", "value": 1})
        assert basic_io.digital_values["pin1"] == 1

    def test_multiple_ids(self, basic_io, mock_ioserver):
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin1", "value": 0})
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin2", "value": 1})
        assert basic_io.digital_values == {"pin1": 0, "pin2": 1}


class TestAnalogWriteHandler:
    def test_stores_analog_value(self, basic_io, mock_ioserver):
        msg = {"id": "adc0", "value": 3.14}
        basic_io.analog_write_handler(mock_ioserver, msg)
        assert basic_io.analog_values["adc0"] == 3.14

    def test_overwrites_analog_value(self, basic_io, mock_ioserver):
        basic_io.analog_write_handler(mock_ioserver, {"id": "adc0", "value": 1.0})
        basic_io.analog_write_handler(mock_ioserver, {"id": "adc0", "value": 2.5})
        assert basic_io.analog_values["adc0"] == 2.5


class TestSendDigitalData:
    def test_sends_correct_message(self, basic_io, mock_ioserver):
        basic_io.send_digital_data("pin1", 1)
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.DigitalIOModel.external_update",
            {"id": "pin1", "value": 1},
        )


class TestSendAnalogData:
    def test_sends_correct_message(self, basic_io, mock_ioserver):
        basic_io.send_analog_data("adc0", 2.5)
        mock_ioserver.send_msg.assert_called_once_with(
            "Peripheral.AnalogIOModel.external_update",
            {"id": "adc0", "value": 2.5},
        )


class TestPrintValues:
    def test_print_digital_values(self, basic_io, mock_ioserver, capsys):
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin1", "value": 1})
        basic_io.print_digital_values()
        out = capsys.readouterr().out
        assert "Digital: pin1: 1" in out

    def test_print_analog_values(self, basic_io, mock_ioserver, capsys):
        basic_io.analog_write_handler(mock_ioserver, {"id": "adc0", "value": 3.14})
        basic_io.print_analog_values()
        out = capsys.readouterr().out
        assert "Analog: adc0: 3.14" in out

    def test_print_values_empty(self, basic_io, capsys):
        basic_io.print_values()
        out = capsys.readouterr().out
        assert out == ""

    def test_print_values_both(self, basic_io, mock_ioserver, capsys):
        basic_io.analog_write_handler(mock_ioserver, {"id": "adc0", "value": 1.0})
        basic_io.digital_write_handler(mock_ioserver, {"id": "pin1", "value": 0})
        basic_io.print_values()
        out = capsys.readouterr().out
        assert "Analog: adc0: 1.0" in out
        assert "Digital: pin1: 0" in out


class TestBasicIOMainBlock:
    """Test the __main__ block command parsing logic."""

    def _run_main_with_inputs(self, input_list):
        """Helper to run the module's __main__ block with given inputs."""
        import halucinator.external_devices.basic_io as bio_mod
        import runpy

        with mock.patch("halucinator.external_devices.ioserver.zmq.Context") as MockCtx:
            ctx_inst = mock.Mock()
            MockCtx.return_value = ctx_inst
            ctx_inst.socket.return_value = mock.Mock()

            with mock.patch("builtins.input", side_effect=input_list), \
                 mock.patch("sys.argv", ["basic_io"]), \
                 mock.patch("halucinator.external_devices.ioserver.IOServer.start"), \
                 mock.patch("halucinator.external_devices.ioserver.IOServer.shutdown"), \
                 mock.patch("halucinator.hal_log.setLogConfig"):
                runpy.run_module(
                    "halucinator.external_devices.basic_io",
                    run_name="__main__",
                )

    def test_main_keyboard_interrupt(self):
        self._run_main_with_inputs(KeyboardInterrupt)

    def test_main_digital_cmd(self):
        self._run_main_with_inputs(["d:pin1:5", KeyboardInterrupt])

    def test_main_analog_cmd(self):
        self._run_main_with_inputs(["a:adc0:3.14", KeyboardInterrupt])

    def test_main_print_cmd(self):
        self._run_main_with_inputs(["p", KeyboardInterrupt])

    def test_main_print_analog(self):
        self._run_main_with_inputs(["pa", KeyboardInterrupt])

    def test_main_print_digital(self):
        self._run_main_with_inputs(["pd", KeyboardInterrupt])

    def test_main_invalid_cmd(self, capsys):
        self._run_main_with_inputs(["xyz", KeyboardInterrupt])
        out = capsys.readouterr().out
        assert "Invalid Command" in out
