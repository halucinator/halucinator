# Copyright 2022 GrammaTech Inc.

from multiprocessing import Manager, Process
from time import sleep
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


class MockRxFromEmulatorNormal:

    printed_lines = Manager().list()

    @classmethod
    def print(cls, *args, **kwargs):
        assert not kwargs
        cls.printed_lines.append(args)

    @classmethod
    def mock(cls):
        with mock.patch("builtins.print", cls.print), mock.patch(
            "zmq.Socket.setsockopt", Socket.setsockopt_string
        ):
            adc.rx_from_emulator(PS_TX_PORT)


class MockRxFromEmulatorWrongName:

    printed_lines = Manager().list()

    @classmethod
    def print(cls, *args, **kwargs):
        assert not kwargs
        cls.printed_lines.append(args)

    @classmethod
    def mock(cls):
        with mock.patch("builtins.print", cls.print), mock.patch(
            "zmq.Socket.setsockopt", Socket.setsockopt_string
        ):
            try:
                adc.rx_from_emulator(PS_TX_PORT)
            except KeyError:
                cls.printed_lines.append("A field name is incorrect")


def rx_from_emulator_test_harness(mock_rx_from_emulator, send_data):
    # Can't run mock_rx_from_emulator in a thread because adc.rx_from_emulator
    # has a blocking zmq.recv_string call in its loop, which makes it impossible
    # to terminate the thread without modifying adc.rx_from_emulator. So run
    # mock_rx_from_emulator in a subprocess, which can be terminated.
    rx_from_emulator_proc = Process(target=mock_rx_from_emulator)
    rx_from_emulator_proc.start()
    # delay to initialize the receiving socket
    sleep(1)
    send_data()
    # delay to complete the rx_from_emulator loop
    sleep(1)
    # Terminate rx_from_emulator_proc directly, as setting
    rx_from_emulator_proc.terminate()
    join_timeout(rx_from_emulator_proc)


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

    assert list(MockRxFromEmulatorNormal.printed_lines) == []
    rx_from_emulator_test_harness(
        MockRxFromEmulatorNormal.mock, send_test_data_from_emulator
    )
    try:
        assert list(MockRxFromEmulatorNormal.printed_lines) == [
            ("Setup ADC Listener",),
            (
                "Got from emulator:",
                "Peripheral.ADC.adc_write adc_id: 1\nvalue: 10\n",
            ),
            ("Id: ", 1, "Value", 10),
        ]
    finally:
        MockRxFromEmulatorNormal.printed_lines = []


def test_external_client_does_not_receive_message_with_incorrect_topic():
    def send_data():
        topic = "OldMacDonaldHadAFarm"
        data = {"id": "pin_id_0", "value": 1}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    assert list(MockRxFromEmulatorNormal.printed_lines) == []
    rx_from_emulator_test_harness(MockRxFromEmulatorNormal.mock, send_data)
    try:
        assert list(MockRxFromEmulatorNormal.printed_lines) != [
            ("Setup ADC Listener",),
        ]
    finally:
        MockRxFromEmulatorNormal.printed_lines = []


def test_sending_message_with_incorrect_field_name_causes_exception():
    def send_data():
        topic = "Peripheral.ADC.adc_write"
        data = {"id": 1, "value": 10}
        PS.__tx_socket__.send_string(PS.encode_zmq_msg(topic, data))

    assert list(MockRxFromEmulatorWrongName.printed_lines) == []
    rx_from_emulator_test_harness(MockRxFromEmulatorWrongName.mock, send_data)
    try:
        assert list(MockRxFromEmulatorWrongName.printed_lines) == [
            ("Setup ADC Listener",),
            (
                "Got from emulator:",
                "Peripheral.ADC.adc_write id: 1\nvalue: 10\n",
            ),
            "A field name is incorrect",
        ]
    finally:
        MockRxFromEmulatorWrongName.printed_lines = []
