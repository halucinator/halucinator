import builtins
from contextlib import contextmanager
from ctypes import c_char_p
from io import StringIO
from multiprocessing import Manager, Process
from time import monotonic, sleep
from unittest import mock

import pytest
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    SetupPeripheralServer,
    join_timeout,
)
from zmq import Socket

import halucinator.peripheral_models.peripheral_server as PS
from halucinator.external_devices import gpio
from halucinator.peripheral_models.gpio import GPIO


def mock_update_gpio_bug_NameError(stringio):
    """
    Expose the expected NameError bug in gpio.update_gpio
    """
    expected_bug_msg = None
    with mock.patch("sys.stdin", stringio):
        try:
            gpio.update_gpio(PS_RX_PORT)
        # The name error is expected bug. The fix is mocked in
        # mock_update_gpio_bug_fixes().
        except NameError as ex:
            if str(ex) == "name 'raw_input' is not defined":
                expected_bug_msg = "NameError: name 'raw_input' is not defined"
            else:
                raise
    return expected_bug_msg


@contextmanager
def raw_input_patched_into_builtins():
    """
    It's unclear how to mock raw_input, so use contextmanager instead
    """
    assert not hasattr(builtins, "raw_input")
    builtins.raw_input = builtins.input
    try:
        yield
    finally:
        del builtins.raw_input


def mock_update_gpio_bug_TypeError(stringio):
    """
    Expose the expected TypeError bug in gpio.update_gpio
    """
    expected_bug_msg = None
    # Mask the raw_input bug.

    with raw_input_patched_into_builtins(), mock.patch("sys.stdin", stringio):
        try:
            gpio.update_gpio(PS_RX_PORT)
        # The type error is expected bug. The fix is mocked in
        # mock_update_gpio_bug_fixes().
        except TypeError as ex:
            if str(ex) == "unicode not allowed, use send_string":
                expected_bug_msg = (
                    "TypeError: unicode not allowed, use send_string"
                )
            else:
                raise
    return expected_bug_msg


def mock_update_gpio_fixed(stringio):
    """
    Mock fixing the NameError and TypeError bugs in gpio.update_gpio
    """
    # Mock the TypeError fix.
    def encode_zmq_msg_encode(topic, data):
        return PS.encode_zmq_msg(topic, data).encode()

    with raw_input_patched_into_builtins(), mock.patch(
        "sys.stdin", stringio
    ), mock.patch(
        "halucinator.external_devices.gpio.encode_zmq_msg",
        encode_zmq_msg_encode,
    ):
        try:
            gpio.update_gpio(PS_RX_PORT)
        # EOFError is expected due to mocking sys.stdin with stringio.
        except EOFError:
            pass
    return None


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.mark.parametrize(
    "mock_update_gpio",
    [
        mock_update_gpio_bug_NameError,
        mock_update_gpio_bug_TypeError,
        mock_update_gpio_fixed,
    ],
)
def test_update_gpio(mock_update_gpio):
    """
    Test gpio.update_gpio bugs (as xfails), and the fix
    """
    gpio_vals = {
        "pin_id_0": 0,
        "pin_id_1": 1,
    }
    stringio = StringIO("\n".join(map(str, sum(gpio_vals.items(), ()))))
    expected_bug_msg = mock_update_gpio(stringio)
    if mock_update_gpio == mock_update_gpio_fixed:
        assert expected_bug_msg is None
        # update_gpio PUBs to the peripheral server, which dispatches to
        # GPIO.ext_pin_change on its own thread — wait for that async hop.
        for _ in range(40):
            if dict(GPIO.gpio_state) == gpio_vals:
                break
            sleep(0.05)
        assert dict(GPIO.gpio_state) == gpio_vals
    else:
        # Xfail the test until it's fixed in the tested code.
        assert expected_bug_msg is not None
        pytest.xfail(expected_bug_msg)


# The rx workers below run in a SUBPROCESS (gpio.rx_from_emulator blocks on
# zmq.recv_string, so a thread cannot be cleanly terminated). They must be
# module-level so they pickle under the "spawn" start method that macOS and
# Windows use, and they receive their shared Manager proxy as an ARGUMENT. A
# Manager object created at import time (or as a class attribute) would be
# re-created inside the spawned child and never seen by the parent — and
# creating a Manager at import re-spawns recursively. Passing the proxy as a
# Process arg keeps the harness correct under both "fork" and "spawn".
def rx_worker_bug_typeerror(expected_bug_msg):
    """
    Capture the (now-fixed) setsockopt TypeError bug in gpio.rx_from_emulator,
    should it ever recur.
    """
    try:
        gpio.rx_from_emulator(PS_TX_PORT)
    except TypeError as ex:
        if str(ex) == "unicode not allowed, use setsockopt_string":
            expected_bug_msg.value = (
                "TypeError: unicode not allowed, use setsockopt_string"
            )
        else:
            raise


def _capture_print(printed_lines):
    def _print(*args, **kwargs):
        assert not kwargs
        printed_lines.append(args)

    return _print


def rx_worker_fixed(printed_lines):
    with mock.patch("builtins.print", _capture_print(printed_lines)):
        gpio.rx_from_emulator(PS_TX_PORT)


def run_rx_worker(worker, send_data, shared, connected=None, ready=None):
    """
    Start ``worker(shared)`` in a spawn-safe subprocess, send data, tear down.
    ``shared`` is a Manager proxy (list or Value) passed as a Process arg so it
    is shared with the child under both the "fork" and "spawn" start methods.

    Timing is driven by the captured output, not fixed sleeps, because both the
    subprocess start (spawn re-imports the module) and zmq delivery latency are
    unbounded under full-suite scheduler load:

    * ``connected() -> bool`` is polled BEFORE publishing, so we wait until the
      child's SUB is actually subscribed (it printed its setup line) — otherwise
      the opening messages are lost to the zmq slow joiner. None → fixed sleep
      (used by the worker that does not capture prints).
    * ``ready() -> bool`` is polled AFTER publishing, for the expected output.
      None → fixed settle.
    """
    proc = Process(target=worker, args=(shared,))
    proc.start()
    deadline = monotonic() + 15
    if connected is not None:
        while monotonic() < deadline and not connected():
            sleep(0.05)
        sleep(0.3)  # let the SUB subscription propagate to the publisher
    else:
        sleep(1)
    send_data()
    while ready is not None and monotonic() < deadline:
        if ready():
            break
        sleep(0.1)
    # brief grace for stragglers (and the whole wait when there is no predicate)
    sleep(0.3 if ready is not None else 2)
    # Terminate directly: gpio.__run_server=False won't break the recv loop.
    proc.terminate()
    join_timeout(proc)


def capture_rx(worker, send_data, expected_len, attempts=3):
    """Run ``worker`` in a subprocess and return the captured lines, using a
    fresh Manager per attempt and retrying on transient message loss.

    Even with the connect settle, subprocess/zmq delivery can drop a message
    under cumulative full-suite load, and these tests assert an EXACT transcript
    — so a lost line must be retried rather than flake the run. A fresh proxy
    per attempt keeps the transcript from accumulating across retries."""
    captured = []
    for _ in range(attempts):
        with Manager() as manager:
            printed_lines = manager.list()
            run_rx_worker(
                worker, send_data, printed_lines,
                connected=lambda: len(printed_lines) >= 1,
                ready=lambda: len(printed_lines) >= expected_len,
            )
            captured = list(printed_lines)
        if len(captured) >= expected_len:
            break
    return captured


def send_test_data_from_emulator():
    """
    Send test data from GPIO
    """
    GPIO.write_pin("pin_id_0", 0)
    GPIO.toggle_pin("pin_id_0")


def test_rx_from_emulator_bug_TypeError():
    """
    Test that the setsockopt TypeError bug is fixed (was: setsockopt with str
    instead of bytes). The bug was fixed by using setsockopt_string in gpio.py.
    """
    with Manager() as manager:
        expected_bug_msg = manager.Value(c_char_p, "")
        run_rx_worker(
            rx_worker_bug_typeerror,
            send_test_data_from_emulator,
            expected_bug_msg,
        )
        # Bug is fixed — setsockopt_string is now used in the source, so no
        # TypeError should occur.
        assert expected_bug_msg.value == ""


def test_rx_from_emulator_bug_fixed():
    """
    Test gpio.rx_from_emulator bug fix.

    KNOWN residual flake (macOS only): this passes in isolation, in every
    directory subset, and on Linux/fork CI, but can intermittently capture only
    the setup line under a *full* ``test/pytest`` run — a cumulative
    peripheral_server/zmq lifecycle issue that survives the fresh-Manager retry
    (so the publish side, not the subprocess, is the culprit). Left as a
    follow-up: the peripheral_server singleton is not fully reset across the
    many module setup/teardown cycles a full run performs.
    """
    captured = capture_rx(rx_worker_fixed, send_test_data_from_emulator, 5)
    assert captured == [
        ("Setup GPIO Listener",),
        (
            "Got from emulator:",
            "Peripheral.GPIO.write_pin id: pin_id_0\nvalue: 0\n",
        ),
        ("Pin: ", "pin_id_0", "Value", 0),
        (
            "Got from emulator:",
            "Peripheral.GPIO.toggle_pin id: pin_id_0\nvalue: 1\n",
        ),
        ("Pin: ", "pin_id_0", "Value", 1),
    ]


def test_rx_from_emulator_subscriptions():
    """
    GPIO.write_pin and GPIO.toggle_pin should be the only subscription topics
    """

    def send_data():
        topic = "OldMacDonaldHadAFarm"
        data = {"id": "pin_id_0", "value": 1}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    captured = capture_rx(rx_worker_fixed, send_data, 2)
    # rx_from_emulator subscribes to '' (ALL topics), so the unrelated message
    # IS received — the missing topic filtering it should have. Xfail the test
    # until rx_from_emulator filters subscription topics.
    assert captured != [("Setup GPIO Listener",)]
    pytest.xfail("rx_from_emulator does not filter subscription topics")
