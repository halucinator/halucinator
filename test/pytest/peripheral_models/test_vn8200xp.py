import pytest
from peripheral_models_helpers import (
    SetupPeripheralServer,
    do_test_receive_from_UARTPublisher,
    do_test_send_to_UARTPublisher,
    setup_ioserver_device,
)

from halucinator.external_devices.vn8200xp import VN8200XP


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_vn8200xp():
    yield from setup_ioserver_device(VN8200XP)


def test_receive_from_UARTPublisher(setup_vn8200xp):
    # post_send_delay is longer than that in test_uart.py because OSError is
    # handled by pmh.print_test.PrintTest(VN8200XP)
    do_test_receive_from_UARTPublisher(setup_vn8200xp.device, 0.5)


def test_send_to_UARTPublisher(setup_vn8200xp):
    do_test_send_to_UARTPublisher(setup_vn8200xp.device, 0.1)
