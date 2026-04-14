import operator
import time
from unittest import mock

import pytest
from peripheral_models_helpers import (
    PS_RX_PORT,
    PS_TX_PORT,
    SetupPeripheralServer,
    assert_,
    join_timeout,
    wait_assert,
)

from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.trigger_interrupt import SendInterrupt


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_send_interrupt():
    ioserver = IOServer(PS_TX_PORT, PS_RX_PORT)
    interrupter = SendInterrupt(ioserver)
    ioserver.start()
    time.sleep(1)
    yield interrupter
    ioserver.shutdown()
    join_timeout(ioserver)
    ioserver.rx_socket.close()
    ioserver.tx_socket.close()


def test_trigger_interrupt_propagates_to_peripheral_sever_qemu(
    setup_send_interrupt,
):
    interrupter = setup_send_interrupt
    num_interrupts = 2
    for num in range(num_interrupts):
        SetupPeripheralServer.qemu.irq_set_qmp.reset_mock()
        interrupter.trigger_interrupt(num)
        wait_assert(
            lambda: SetupPeripheralServer.qemu.irq_set_qmp.assert_called_once_with(
                num
            )
        )


def test_set_vector_base_propagates_to_peripheral_sever_qemu(
    setup_send_interrupt,
):
    interrupter = setup_send_interrupt
    base_addrs = [0x01, 0x02]
    ioserver_send_msg = IOServer.send_msg

    def send_msg(self, topic, data):
        # Add a dummy "num" value to data, to sidestep the bug marked with 'should be
        # msg["base"] rather than msg["num"]' in peripheral_server.run_server.
        data["num"] = 0
        ioserver_send_msg(self, topic, data)

    for base_addr in base_addrs:
        SetupPeripheralServer.qemu.set_vector_table_base.reset_mock()
        with mock.patch.object(IOServer, "send_msg", send_msg):
            interrupter.set_vector_base(base_addr)
        wait_assert(
            lambda: SetupPeripheralServer.qemu.set_vector_table_base.assert_called_once_with(
                base_addr
            ),
        )


def test_set_vector_base_bug(setup_send_interrupt):
    assert SetupPeripheralServer.peripheral_server_thread.xfail_msgs == []
    interrupter = setup_send_interrupt
    base_addr = 0x01
    interrupter.set_vector_base(base_addr)
    time.sleep(0.1)
    # Xfail the test until it's fixed in the tested code.
    wait_assert(
        lambda: assert_(
            operator.__not__,
            (SetupPeripheralServer.peripheral_server_thread.is_alive(),),
        )
    )
    assert SetupPeripheralServer.peripheral_server_thread.xfail_msgs
    pytest.xfail(
        "\n"
        + "\n".join(SetupPeripheralServer.peripheral_server_thread.xfail_msgs)
    )
