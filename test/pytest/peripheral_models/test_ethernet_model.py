# Test ethernet peripheral model behavior not covered by interactions with
# external devices.

import time

import pytest
from peripheral_models_helpers import (
    SetupPeripheralServer,
    assert_,
    fix_server_shutdown,
    wait_assert,
)

from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models.ethernet import (
    EthernetMessage,
    EthernetModel,
)
from halucinator.peripheral_models.interrupts import Interrupts


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


def test_disable_rx_isr_resets_rx_isr_enabled():
    EthernetModel.rx_isr_enabled = True
    EthernetModel.disable_rx_isr(1)
    assert EthernetModel.rx_isr_enabled is False


def test_enable_rx_isr_sets_rx_isr_enabled():
    EthernetModel.rx_isr_enabled = False
    EthernetModel.enable_rx_isr("eth0")
    assert EthernetModel.rx_isr_enabled is True


@pytest.fixture()
def ethernet_model_recv_message():
    EthernetModel.frame_queues.clear()
    EthernetModel.frame_times.clear()
    ioserver = IOServer()
    time.sleep(0.2)
    msg = EthernetMessage(interface_id="interface_id", frame="frame".encode())
    ioserver.send_msg("Peripheral.EthernetModel.rx_frame", msg)
    wait_assert(lambda: assert_(len, (EthernetModel.frame_queues,)))
    yield msg, EthernetModel.frame_times[msg["interface_id"]][0]
    fix_server_shutdown(ioserver.rx_socket, ioserver.tx_socket, 0)


def test_enable_rx_isr_triggers_interupt(ethernet_model_recv_message):
    # The interrupt_source value is hardcoded in EthernetModel.enable_rx_isr.
    interrupt_source = "Ethernet_RX_Frame"
    Interrupts.clear_active(interrupt_source)
    assert Interrupts.Active_Interrupts[interrupt_source] is False
    SetupPeripheralServer.qemu.irq_set_qmp.reset_mock()
    # Set EthernetModel.rx_frame_isr to an arbitrary value.
    EthernetModel.rx_frame_isr = 20
    msg, _ = ethernet_model_recv_message
    EthernetModel.enable_rx_isr(msg["interface_id"])
    assert Interrupts.Active_Interrupts[interrupt_source] is True
    SetupPeripheralServer.qemu.irq_set_qmp.assert_called_once_with(
        EthernetModel.rx_frame_isr
    )


def test_get_rx_frame_yields_frame_time(ethernet_model_recv_message):
    msg, msg_time = ethernet_model_recv_message
    assert EthernetModel.get_rx_frame(msg["interface_id"], True) == (
        msg["frame"],
        msg_time,
    )
    assert EthernetModel.get_rx_frame(msg["interface_id"], True) == (
        None,
        None,
    )


def test_get_rx_frame_yields_frame(ethernet_model_recv_message):
    msg, msg_time = ethernet_model_recv_message
    assert (
        EthernetModel.get_rx_frame(msg["interface_id"], False) == msg["frame"]
    )
    assert EthernetModel.get_rx_frame(msg["interface_id"], False) == None


def test_get_rx_frame_and_time_yields_frame_and_time(
    ethernet_model_recv_message,
):
    msg, msg_time = ethernet_model_recv_message
    assert EthernetModel.get_rx_frame_and_time(msg["interface_id"]) == (
        msg["frame"],
        msg_time,
    )
    assert EthernetModel.get_rx_frame_and_time(msg["interface_id"]) == (
        None,
        None,
    )


def test_get_rx_frame_only_yields_frame(ethernet_model_recv_message):
    msg, msg_time = ethernet_model_recv_message
    assert EthernetModel.get_rx_frame(msg["interface_id"]) == msg["frame"]
    assert EthernetModel.get_rx_frame(msg["interface_id"]) == None


def test_get_frame_info_yields_frame_info(ethernet_model_recv_message):
    msg, _ = ethernet_model_recv_message
    assert EthernetModel.get_frame_info(msg["interface_id"]) == (
        1,  # queue size
        len(msg["frame"]),  # queue[0] size
    )
    EthernetModel.get_rx_frame_only(msg["interface_id"])
    assert EthernetModel.get_frame_info(msg["interface_id"]) == (0, 0)
