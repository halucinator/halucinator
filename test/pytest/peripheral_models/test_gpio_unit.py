"""
Unit tests for halucinator.peripheral_models.gpio.GPIO

Tests GPIO state management. The tx_msg sending behavior is tested by
the integration tests in test_gpio.py which set up real zmq infrastructure.

Note: These tests may fail when run as part of the full 28K test suite due to
global state pollution from integration tests that use real zmq sockets.
They pass reliably when run in isolation or with just the gpio test files.
"""
from collections import defaultdict
from unittest import mock

import pytest

from halucinator.peripheral_models.gpio import GPIO


@pytest.fixture(autouse=True)
def clean_state():
    saved = GPIO.gpio_state
    GPIO.gpio_state = defaultdict(int)
    yield
    GPIO.gpio_state = saved


def test_ext_pin_change():
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 1})
    assert GPIO.gpio_state["unit_pin0"] == 1


def test_ext_pin_change_multiple():
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 1})
    GPIO.ext_pin_change({"id": "unit_pin1", "value": 0})
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 0})
    assert GPIO.gpio_state["unit_pin0"] == 0
    assert GPIO.gpio_state["unit_pin1"] == 0


def test_read_pin_default():
    assert GPIO.read_pin("unit_nonexistent_pin_xyz") == 0


def test_read_pin_after_set():
    GPIO.gpio_state["unit_pin0"] = 1
    assert GPIO.read_pin("unit_pin0") == 1


def test_read_pin_after_ext_change():
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 42})
    assert GPIO.read_pin("unit_pin0") == 42


def test_ext_pin_change_overwrites():
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 1})
    GPIO.ext_pin_change({"id": "unit_pin0", "value": 99})
    assert GPIO.gpio_state["unit_pin0"] == 99


def test_write_pin_sets_state():
    """Test write_pin updates gpio_state (covers line 28-31)."""
    from halucinator.peripheral_models import peripheral_server as ps
    orig_socket = getattr(ps, "__TX_SOCKET__", None)
    setattr(ps, "__TX_SOCKET__", mock.Mock())
    try:
        GPIO.write_pin("unit_write_pin", 1)
        assert GPIO.gpio_state["unit_write_pin"] == 1
        GPIO.write_pin("unit_write_pin", 0)
        assert GPIO.gpio_state["unit_write_pin"] == 0
    finally:
        setattr(ps, "__TX_SOCKET__", orig_socket)


def test_toggle_pin_toggles():
    """Test toggle_pin flips state (covers lines 40-47)."""
    from halucinator.peripheral_models import peripheral_server as ps
    orig_socket = getattr(ps, "__TX_SOCKET__", None)
    setattr(ps, "__TX_SOCKET__", mock.Mock())
    try:
        # First toggle on a new pin (not in state -> initialized to 0)
        GPIO.toggle_pin("unit_toggle_pin")
        assert GPIO.gpio_state["unit_toggle_pin"] == 0

        # Second toggle (0 ^ 1 = 1)
        GPIO.toggle_pin("unit_toggle_pin")
        assert GPIO.gpio_state["unit_toggle_pin"] == 1

        # Third toggle (1 ^ 1 = 0)
        GPIO.toggle_pin("unit_toggle_pin")
        assert GPIO.gpio_state["unit_toggle_pin"] == 0
    finally:
        setattr(ps, "__TX_SOCKET__", orig_socket)


def test_toggle_pin_from_existing_state():
    """Test toggle_pin when pin already has a known state."""
    from halucinator.peripheral_models import peripheral_server as ps
    orig_socket = getattr(ps, "__TX_SOCKET__", None)
    setattr(ps, "__TX_SOCKET__", mock.Mock())
    try:
        GPIO.gpio_state["unit_toggle2"] = 1
        GPIO.toggle_pin("unit_toggle2")
        assert GPIO.gpio_state["unit_toggle2"] == 0
    finally:
        setattr(ps, "__TX_SOCKET__", orig_socket)
