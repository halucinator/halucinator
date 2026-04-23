"""
Tests for halucinator.peripheral_models.interrupts.Interrupts
"""
from collections import defaultdict
from unittest import mock

import pytest

from halucinator.peripheral_models.interrupts import Interrupts
import halucinator.peripheral_models.peripheral_server as peripheral_server


@pytest.fixture(autouse=True)
def clean_state():
    """Reset Interrupts class state between tests."""
    Interrupts.Active_Interrupts = defaultdict(bool)
    Interrupts.active = defaultdict(bool)
    Interrupts.enabled = defaultdict(bool)
    yield


# ---------- interrupt_request (rx handler) ----------

@mock.patch.object(Interrupts, "set_active_qmp")
def test_interrupt_request_with_num(mock_set):
    msg = {"num": 10}
    Interrupts.interrupt_request(msg)
    mock_set.assert_called_once_with(10)


@mock.patch.object(Interrupts, "set_active_qmp")
def test_interrupt_request_without_num(mock_set):
    msg = {"other": "data"}
    Interrupts.interrupt_request(msg)
    mock_set.assert_not_called()


# ---------- trigger_interrupt ----------

@mock.patch.object(peripheral_server, "trigger_interrupt")
def test_trigger_interrupt_with_source(mock_trigger):
    Interrupts.trigger_interrupt(5, source="timer")
    assert Interrupts.Active_Interrupts["timer"] is True
    mock_trigger.assert_called_once_with(5)


@mock.patch.object(peripheral_server, "trigger_interrupt")
def test_trigger_interrupt_no_source(mock_trigger):
    Interrupts.trigger_interrupt(5)
    mock_trigger.assert_called_once_with(5)


# ---------- set_active / clear_active / is_active ----------

def test_set_active():
    Interrupts.set_active("key1")
    assert Interrupts.Active_Interrupts["key1"] is True


def test_clear_active():
    Interrupts.Active_Interrupts["key1"] = True
    Interrupts.clear_active("key1")
    assert Interrupts.Active_Interrupts["key1"] is False


def test_is_active():
    assert Interrupts.is_active("key1") is False
    Interrupts.Active_Interrupts["key1"] = True
    assert Interrupts.is_active("key1") is True


# ---------- set_active_qmp / clear_active_qmp ----------

@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_set_active_qmp_enabled(mock_irq):
    Interrupts.enabled[10] = True
    Interrupts.set_active_qmp(10)
    assert Interrupts.active[10] is True
    mock_irq.assert_called_once_with(10)


@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_set_active_qmp_not_enabled(mock_irq):
    Interrupts.enabled[10] = False
    Interrupts.set_active_qmp(10)
    assert Interrupts.active[10] is True
    mock_irq.assert_not_called()


@mock.patch.object(peripheral_server, "irq_clear_qmp")
def test_clear_active_qmp(mock_irq):
    Interrupts.active[10] = True
    Interrupts.clear_active_qmp(10)
    assert Interrupts.active[10] is False
    mock_irq.assert_called_once_with(10)


# ---------- set_active_bp / clear_active_bp ----------

@mock.patch.object(peripheral_server, "irq_set_bp")
def test_set_active_bp_enabled(mock_irq):
    Interrupts.enabled[10] = True
    Interrupts.set_active_bp(10)
    assert Interrupts.active[10] is True
    mock_irq.assert_called_once_with(10)


@mock.patch.object(peripheral_server, "irq_set_bp")
def test_set_active_bp_not_enabled(mock_irq):
    Interrupts.enabled[10] = False
    Interrupts.set_active_bp(10)
    assert Interrupts.active[10] is True
    mock_irq.assert_not_called()


@mock.patch.object(peripheral_server, "irq_clear_bp")
def test_clear_active_bp(mock_irq):
    Interrupts.active[10] = True
    Interrupts.clear_active_bp(10)
    assert Interrupts.active[10] is False
    mock_irq.assert_called_once_with(10)


# ---------- _trigger_interrupt_qmp / _trigger_interrupt_bp ----------

@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_trigger_interrupt_qmp_fires_when_enabled_and_active(mock_irq):
    Interrupts.enabled[5] = True
    Interrupts.active[5] = True
    Interrupts._trigger_interrupt_qmp(5)
    mock_irq.assert_called_once_with(5)


@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_trigger_interrupt_qmp_no_fire_when_not_enabled(mock_irq):
    Interrupts.enabled[5] = False
    Interrupts.active[5] = True
    Interrupts._trigger_interrupt_qmp(5)
    mock_irq.assert_not_called()


@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_trigger_interrupt_qmp_no_fire_when_not_active(mock_irq):
    Interrupts.enabled[5] = True
    Interrupts.active[5] = False
    Interrupts._trigger_interrupt_qmp(5)
    mock_irq.assert_not_called()


@mock.patch.object(peripheral_server, "irq_set_bp")
def test_trigger_interrupt_bp_fires_when_enabled_and_active(mock_irq):
    Interrupts.enabled[5] = True
    Interrupts.active[5] = True
    Interrupts._trigger_interrupt_bp(5)
    mock_irq.assert_called_once_with(5)


@mock.patch.object(peripheral_server, "irq_set_bp")
def test_trigger_interrupt_bp_no_fire_when_not_enabled(mock_irq):
    Interrupts.enabled[5] = False
    Interrupts.active[5] = True
    Interrupts._trigger_interrupt_bp(5)
    mock_irq.assert_not_called()


# ---------- enable_bp / enable_qmp ----------

@mock.patch.object(peripheral_server, "irq_set_bp")
def test_enable_bp(mock_irq):
    Interrupts.active[10] = True
    Interrupts.enable_bp(10)
    assert Interrupts.enabled[10] is True
    mock_irq.assert_called_once_with(10)


@mock.patch.object(peripheral_server, "irq_set_bp")
def test_enable_bp_not_active(mock_irq):
    Interrupts.active[10] = False
    Interrupts.enable_bp(10)
    assert Interrupts.enabled[10] is True
    mock_irq.assert_not_called()


@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_enable_qmp(mock_irq):
    Interrupts.active[10] = True
    Interrupts.enable_qmp(10)
    assert Interrupts.enabled[10] is True
    mock_irq.assert_called_once_with(10)


@mock.patch.object(peripheral_server, "irq_set_qmp")
def test_enable_qmp_not_active(mock_irq):
    Interrupts.active[10] = False
    Interrupts.enable_qmp(10)
    assert Interrupts.enabled[10] is True
    mock_irq.assert_not_called()


# ---------- disable_bp / disable_qmp ----------

@mock.patch.object(peripheral_server, "irq_clear_bp")
def test_disable_bp(mock_irq):
    Interrupts.enabled[10] = True
    Interrupts.disable_bp(10)
    assert Interrupts.enabled[10] is False
    mock_irq.assert_called_once_with(10)


@mock.patch.object(peripheral_server, "irq_disable_qmp")
def test_disable_qmp(mock_irq):
    Interrupts.enabled[10] = True
    Interrupts.disable_qmp(10)
    assert Interrupts.enabled[10] is False
    mock_irq.assert_called_once_with(10)


# ---------- get_active_irqs / get_first_irq ----------

def test_get_active_irqs_empty():
    assert Interrupts.get_active_irqs() == set()


def test_get_active_irqs():
    Interrupts.active[1] = True
    Interrupts.active[2] = True
    Interrupts.active[3] = False
    Interrupts.enabled[1] = True
    Interrupts.enabled[2] = False
    Interrupts.enabled[3] = True
    # Only irq 1 is both active and enabled
    assert Interrupts.get_active_irqs() == {1}


def test_get_active_irqs_multiple():
    Interrupts.active[1] = True
    Interrupts.active[2] = True
    Interrupts.enabled[1] = True
    Interrupts.enabled[2] = True
    assert Interrupts.get_active_irqs() == {1, 2}


def test_get_first_irq_none():
    assert Interrupts.get_first_irq() is None


def test_get_first_irq_lowest_first():
    Interrupts.active[5] = True
    Interrupts.active[3] = True
    Interrupts.enabled[5] = True
    Interrupts.enabled[3] = True
    assert Interrupts.get_first_irq(highest_first=False) == 3


def test_get_first_irq_highest_first():
    Interrupts.active[5] = True
    Interrupts.active[3] = True
    Interrupts.enabled[5] = True
    Interrupts.enabled[3] = True
    assert Interrupts.get_first_irq(highest_first=True) == 5


def test_get_first_irq_single():
    Interrupts.active[7] = True
    Interrupts.enabled[7] = True
    assert Interrupts.get_first_irq() == 7
