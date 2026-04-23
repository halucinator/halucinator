import builtins
from contextlib import contextmanager
from ctypes import c_char_p
from io import StringIO
from multiprocessing import Manager, Process
from time import sleep
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
        assert GPIO.gpio_state == gpio_vals
    else:
        # Xfail the test until it's fixed in the tested code.
        assert expected_bug_msg is not None
        pytest.xfail(expected_bug_msg)


class MockRxFromEmulatorBugTypeError:
    """
    Expose the expected TypeError bug in gpio.rx_from_emulator
    """

    expected_bug_msg = Manager().Value(c_char_p, "")

    @classmethod
    def mock(cls):
        try:
            gpio.rx_from_emulator(PS_TX_PORT)
        # The type error is expected bug. The fix is mocked in
        # mock_rx_from_emulator_fixed().
        except TypeError as ex:
            if str(ex) == "unicode not allowed, use setsockopt_string":
                cls.expected_bug_msg.value = (
                    "TypeError: unicode not allowed, use setsockopt_string"
                )
            else:
                raise


class MockRxFromEmulatorFixed:
    """
    Mock for testing gpio.rx_from_emulator (setsockopt_string bug is fixed in source)
    """

    printed_lines = Manager().list()

    @classmethod
    def print(cls, *args, **kwargs):
        assert not kwargs
        cls.printed_lines.append(args)

    @classmethod
    def mock(cls):
        with mock.patch("builtins.print", cls.print):
            gpio.rx_from_emulator(PS_TX_PORT)


def rx_from_emulator_test_harness(mock_rx_from_emulator, send_data):
    """
    rx_from_emulator test harness
    """
    # Can't run mock_rx_from_emulator in a thread because gpio.rx_from_emulator
    # has a blocking zmq.recv_string call in its loop, which makes it impossible
    # to terminate the thread without modifying gpio.rx_from_emulator. So run
    # mock_rx_from_emulator in a subprocess, which can be terminated.
    rx_from_emulator_proc = Process(target=mock_rx_from_emulator)
    rx_from_emulator_proc.start()
    # delay to initialize the receiving socket
    sleep(0.1)
    send_data()
    # delay to complete the rx_from_emulator loop
    sleep(0.1)
    # Terminate rx_from_emulator_proc directly, as setting
    # gpio.__run_server=False won't break the gpio.rx_from_emulator loop.
    rx_from_emulator_proc.terminate()
    join_timeout(rx_from_emulator_proc)


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

    assert MockRxFromEmulatorBugTypeError.expected_bug_msg.value == ""
    rx_from_emulator_test_harness(
        MockRxFromEmulatorBugTypeError.mock, send_test_data_from_emulator
    )
    try:
        # Bug is fixed — setsockopt_string is now used in the source,
        # so no TypeError should occur.
        assert MockRxFromEmulatorBugTypeError.expected_bug_msg.value == ""
    finally:
        MockRxFromEmulatorBugTypeError.expected_bug_msg.value = ""


def test_rx_from_emulator_bug_fixed():
    """
    Test gpio.rx_from_emulator bug fix.
    """

    assert list(MockRxFromEmulatorFixed.printed_lines) == []
    rx_from_emulator_test_harness(
        MockRxFromEmulatorFixed.mock, send_test_data_from_emulator
    )
    try:
        assert list(MockRxFromEmulatorFixed.printed_lines) == [
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
    finally:
        MockRxFromEmulatorFixed.printed_lines = []


def test_rx_from_emulator_subscriptions():
    """
    GPIO.write_pin and GPIO.toggle_pin should be the only subscription topics
    """

    def send_data():
        topic = "OldMacDonaldHadAFarm"
        data = {"id": "pin_id_0", "value": 1}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    assert list(MockRxFromEmulatorFixed.printed_lines) == []
    rx_from_emulator_test_harness(MockRxFromEmulatorFixed.mock, send_data)
    try:
        # Xfail the test until it's fixed in the tested code.
        assert list(MockRxFromEmulatorFixed.printed_lines) != [
            ("Setup GPIO Listener",),
        ]
        pytest.xfail("rx_from_emulator does not filter subscription topics")
    finally:
        MockRxFromEmulatorFixed.printed_lines = []
