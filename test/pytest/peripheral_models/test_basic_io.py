"""
Tests for halucinator.peripheral_models.basic_io (DigitalIOModel and AnalogIOModel)
"""
from collections import defaultdict
from unittest import mock

import pytest

from halucinator.peripheral_models.basic_io import AnalogIOModel, DigitalIOModel


@pytest.fixture(autouse=True)
def clean_state():
    """Reset model state between tests."""
    DigitalIOModel.values = defaultdict(int)
    AnalogIOModel.values = defaultdict(float)
    yield


# ===================== DigitalIOModel tests =====================

class TestDigitalIOModel:

    def test_get_id_int(self):
        assert DigitalIOModel.get_id(5) == 5

    def test_get_id_str_numeric(self):
        assert DigitalIOModel.get_id("42") == 42

    def test_get_id_str_non_numeric(self):
        assert DigitalIOModel.get_id("gpio_a") == "gpio_a"

    def test_get_value_default(self):
        assert DigitalIOModel.get_value("ch0") == 0

    def test_get_value_after_set(self):
        DigitalIOModel.values["ch0"] = 1
        assert DigitalIOModel.get_value("ch0") == 1

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_set_value(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        DigitalIOModel.set_value("ch0", 1)
        # set_value delegates to internal_update which only sends the msg
        mock_socket.send_string.assert_called_once()

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_set_value_numeric_str(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        DigitalIOModel.set_value("3", 1)
        mock_socket.send_string.assert_called_once()
        call_arg = mock_socket.send_string.call_args[0][0]
        assert "DigitalIOModel.internal_update" in call_arg

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_internal_update_sends_msg(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        DigitalIOModel.internal_update("ch0", 42)
        mock_socket.send_string.assert_called_once()
        call_arg = mock_socket.send_string.call_args[0][0]
        assert "DigitalIOModel.internal_update" in call_arg

    def test_external_update(self):
        msg = {"id": "ch0", "value": 1}
        DigitalIOModel.external_update(msg)
        assert DigitalIOModel.values["ch0"] == 1

    def test_external_update_numeric_id(self):
        msg = {"id": "5", "value": 99}
        DigitalIOModel.external_update(msg)
        assert DigitalIOModel.values[5] == 99

    def test_external_update_bad_msg(self):
        """Missing keys should not raise, just log warning."""
        msg = {"bad_key": "value"}
        DigitalIOModel.external_update(msg)  # should not raise

    def test_external_update_missing_value(self):
        msg = {"id": "ch0"}
        DigitalIOModel.external_update(msg)  # should not raise


# ===================== AnalogIOModel tests =====================

class TestAnalogIOModel:

    def test_get_id_int(self):
        assert AnalogIOModel.get_id(5) == 5

    def test_get_id_str_numeric(self):
        assert AnalogIOModel.get_id("42") == 42

    def test_get_id_str_non_numeric(self):
        assert AnalogIOModel.get_id("adc_ch") == "adc_ch"

    def test_get_value_default(self):
        assert AnalogIOModel.get_value("ch0") == 0.0

    def test_get_value_after_set(self):
        AnalogIOModel.values["ch0"] = 3.14
        assert AnalogIOModel.get_value("ch0") == 3.14

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_set_value(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        AnalogIOModel.set_value("ch0", 2.718)
        # set_value delegates to internal_update which only sends the msg
        mock_socket.send_string.assert_called_once()

    @mock.patch("halucinator.peripheral_models.peripheral_server.__TX_SOCKET__")
    def test_internal_update_sends_msg(self, mock_socket):
        mock_socket.send_string = mock.Mock()
        AnalogIOModel.internal_update("ch0", 1.5)
        mock_socket.send_string.assert_called_once()
        call_arg = mock_socket.send_string.call_args[0][0]
        assert "AnalogIOModel.internal_update" in call_arg

    def test_external_update(self):
        msg = {"id": "ch0", "value": 5.5}
        AnalogIOModel.external_update(msg)
        assert AnalogIOModel.values["ch0"] == 5.5

    def test_external_update_numeric_id(self):
        msg = {"id": "7", "value": 1.23}
        AnalogIOModel.external_update(msg)
        assert AnalogIOModel.values[7] == 1.23

    def test_external_update_bad_msg(self):
        msg = {"wrong": "keys"}
        AnalogIOModel.external_update(msg)  # should not raise

    def test_external_update_missing_value(self):
        msg = {"id": "ch0"}
        AnalogIOModel.external_update(msg)  # should not raise
