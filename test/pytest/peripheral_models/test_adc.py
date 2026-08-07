# Copyright 2022 GrammaTech Inc.

from multiprocessing import Manager, Process
from time import monotonic, sleep
from unittest import mock

import pytest
from peripheral_models_helpers import (
    PS_TX_PORT,
    SetupPeripheralServer,
    join_timeout,
)
from zmq import Socket

import halucinator.peripheral_models.peripheral_server as PS
from halucinator.external_devices import adc
from halucinator.peripheral_models.adc import ADC


# The rx workers below run in a SUBPROCESS (adc.rx_from_emulator blocks on
# zmq.recv_string, so a thread cannot be cleanly terminated). They must be
# module-level so they pickle under the "spawn" start method that macOS and
# Windows use, and they receive the shared ``printed_lines`` proxy as an
# ARGUMENT. A Manager list created at import time (or as a class attribute)
# would be re-created inside the spawned child and never seen by the parent —
# and creating a Manager at import re-spawns recursively ("an attempt has been
# made to start a new process before ... bootstrapping"). Passing the proxy as
# a Process arg keeps the harness correct under both "fork" and "spawn".
def _capture_print(printed_lines):
    def _print(*args, **kwargs):
        assert not kwargs
        printed_lines.append(args)

    return _print


def rx_worker_normal(printed_lines):
    with mock.patch("builtins.print", _capture_print(printed_lines)), mock.patch(
        "zmq.Socket.setsockopt", Socket.setsockopt_string
    ):
        adc.rx_from_emulator(PS_TX_PORT)


def rx_worker_wrong_field_name(printed_lines):
    with mock.patch("builtins.print", _capture_print(printed_lines)), mock.patch(
        "zmq.Socket.setsockopt", Socket.setsockopt_string
    ):
        try:
            adc.rx_from_emulator(PS_TX_PORT)
        except KeyError:
            printed_lines.append("A field name is incorrect")


def rx_from_emulator_test_harness(worker, send_data, ready=None):
    """Run ``worker(printed_lines)`` in a spawn-safe subprocess and return the
    captured lines. The Manager proxy is passed as a Process arg so it is shared
    with the child under both the "fork" (Linux) and "spawn" (macOS/Windows)
    start methods.

    Timing is driven by the captured output, not fixed sleeps, because both the
    subprocess start (spawn re-imports the module) and the zmq delivery latency
    are unbounded under full-suite scheduler load:

    * Before publishing, wait until the child has printed its "Setup ... Listener"
      line (``len(printed_lines) >= 1``) so the SUB is actually subscribed —
      publishing earlier loses the opening messages to the zmq slow joiner.
    * After publishing, ``ready(printed_lines)`` polls for the expected output.
      When ``ready`` is None (a negative test asserting nothing extra arrives) a
      fixed settle is used instead. A positive test retries with a fresh proxy
      on transient message loss (delivery can still drop a line under cumulative
      full-suite load), since the assertions are exact transcripts."""
    attempts = 3 if ready is not None else 1
    captured = []
    for _ in range(attempts):
        with Manager() as manager:
            printed_lines = manager.list()
            proc = Process(target=worker, args=(printed_lines,))
            proc.start()
            deadline = monotonic() + 15
            while monotonic() < deadline and len(printed_lines) < 1:
                sleep(0.05)
            sleep(0.3)  # let the SUB subscription propagate to the publisher
            send_data()
            while ready is not None and monotonic() < deadline:
                if ready(printed_lines):
                    break
                sleep(0.1)
            # brief grace for stragglers (and the whole wait for negative tests)
            sleep(0.3 if ready is not None else 2)
            proc.terminate()
            join_timeout(proc)
            captured = list(printed_lines)
        if ready is None or ready(captured):
            break
    return captured


def send_test_data_from_emulator():
    ADC.adc_write(1, 10)


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.mark.parametrize("adc_id", [1, 2, 4, 10])
@pytest.mark.parametrize("adc_value", [10, 100, 101, 234])
def test_ext_adc_change_writes_data_from_message_correctly(adc_id, adc_value):
    ADC.adc_state = {}
    ADC.ext_adc_change({"adc_id": adc_id, "value": adc_value})
    assert ADC.adc_state == {adc_id: adc_value}


@pytest.mark.parametrize("adc_id", [1, 2, 4, 10])
@pytest.mark.parametrize("adc_value", [10, 100, 101, 234])
def test_adc_read_returns_value_correctly(adc_id, adc_value):
    ADC.adc_state = {adc_id: adc_value}
    assert ADC.adc_read(adc_id) == adc_value


def test_external_client_receives_message_with_correct_topic():
    printed_lines = rx_from_emulator_test_harness(
        rx_worker_normal, send_test_data_from_emulator,
        ready=lambda pl: len(pl) >= 3,
    )
    assert printed_lines == [
        ("Setup ADC Listener",),
        (
            "Got from emulator:",
            "Peripheral.ADC.adc_write adc_id: 1\nvalue: 10\n",
        ),
        ("Id: ", 1, "Value", 10),
    ]


def test_external_client_does_not_receive_message_with_incorrect_topic():
    def send_data():
        topic = "OldMacDonaldHadAFarm"
        data = {"id": "pin_id_0", "value": 1}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    printed_lines = rx_from_emulator_test_harness(rx_worker_normal, send_data)
    # adc.rx_from_emulator subscribes to "Peripheral.ADC.adc_write", so a
    # message on an unrelated topic is filtered by zmq and never delivered:
    # only the listener's setup line is printed, nothing "Got from emulator:".
    assert printed_lines == [("Setup ADC Listener",)]


def test_sending_message_with_incorrect_field_name_causes_exception():
    def send_data():
        topic = "Peripheral.ADC.adc_write"
        data = {"id": 1, "value": 10}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    printed_lines = rx_from_emulator_test_harness(
        rx_worker_wrong_field_name, send_data,
        ready=lambda pl: len(pl) >= 3,
    )
    assert printed_lines == [
        ("Setup ADC Listener",),
        (
            "Got from emulator:",
            "Peripheral.ADC.adc_write id: 1\nvalue: 10\n",
        ),
        "A field name is incorrect",
    ]
