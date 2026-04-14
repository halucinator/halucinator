import binascii
import signal
import time
from collections import deque
from io import StringIO
from multiprocessing import Process
from os import kill
from time import sleep
from unittest import mock

import pytest
from delayed_signal_handling import AtomicCallCount
from main_mock import MainMock
from peripheral_models_helpers import (
    SetupPeripheralServer,
    assert_,
    fix_server_shutdown,
    join_timeout,
    wait_assert,
)

from halucinator.external_devices.IEEE802_15_4 import (
    IEEE802_15_4 as device_IEEE802_15_4,
)
from halucinator.external_devices.IEEE802_15_4 import main
from halucinator.external_devices.ioserver import IOServer
from halucinator.peripheral_models.ieee802_15_4 import (
    IEEE802_15_4 as model_IEEE802_15_4,
)
from halucinator.peripheral_models.ieee802_15_4 import IEEE802_15_4Message
from halucinator.peripheral_models.interrupts import Interrupts


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


def test_main_interrupt():
    hexlified_frames = [f"deadbeef0{idx}" for idx in range(10)]
    counted_method = AtomicCallCount(device_IEEE802_15_4.received_frame, 0.1)
    main_mock = MainMock(
        main, [], StringIO("\n".join(hexlified_frames)), counted_method
    )
    proc = Process(target=main_mock.main_mock)
    model_IEEE802_15_4.frame_queue.clear()
    proc.start()
    # Account for the input loop start delay in IEEE802_15_4.main.
    after_loop_starts_sec = 2.1
    sleep(after_loop_starts_sec)
    # Let the input loop get inputs from hexlified_frames for some time.
    loop_time_sec = 0.2
    # In order to cover KeyboardInterrupt, we need to still have pending
    # IOString inputs prior to triggering SIGINT. Otherwise, EOFError would be
    # triggered first.
    num_inputs_before_sigint_upper_bound = (
        loop_time_sec / counted_method.wait_sec
    )
    assert len(hexlified_frames) > num_inputs_before_sigint_upper_bound
    sleep(loop_time_sec)
    # Raise KeyboardInterrupt in IEEE802_15_4.main.
    kill(proc.pid, signal.SIGINT)
    join_timeout(proc)
    assert counted_method.num_calls >= 2
    assert len(model_IEEE802_15_4.frame_queue) == counted_method.num_calls
    assert list(model_IEEE802_15_4.frame_queue) == [
        binascii.unhexlify(frame)
        for frame in hexlified_frames[: counted_method.num_calls]
    ]


def test_main_num_ports():
    # Wait after starting session-level peripheral server
    counted_method = AtomicCallCount(device_IEEE802_15_4.received_frame, 0.1)
    main_mock = MainMock(main, ["-t", ""], StringIO(""), counted_method)
    proc = Process(target=main_mock.main_mock)
    proc.start()
    join_timeout(proc)
    assert not proc.is_alive()
    assert main_mock.quit_flag


def test_disable_rx_isr_resets_rx_isr_enabled():
    model_IEEE802_15_4.rx_isr_enabled = True
    model_IEEE802_15_4.disable_rx_isr("eth0")
    assert model_IEEE802_15_4.rx_isr_enabled is False


def test_enable_rx_isr_sets_rx_isr_enabled():
    model_IEEE802_15_4.rx_isr_enabled = False
    model_IEEE802_15_4.enable_rx_isr("eth0")
    assert model_IEEE802_15_4.rx_isr_enabled is True


@pytest.fixture()
def model_IEEE802_15_4_recv_message():
    model_IEEE802_15_4.frame_queue.clear()
    model_IEEE802_15_4.frame_time.clear()
    ioserver = IOServer()
    time.sleep(0.2)
    msg = IEEE802_15_4Message(frame="frame".encode())
    ioserver.send_msg("Peripheral.IEEE802_15_4.rx_frame", msg)
    wait_assert(lambda: assert_(len, (model_IEEE802_15_4.frame_queue,)))
    yield msg, model_IEEE802_15_4.frame_time[0]
    fix_server_shutdown(ioserver.rx_socket, ioserver.tx_socket, 0)


def test_enable_rx_isr_triggers_interupt(model_IEEE802_15_4_recv_message):
    Interrupts.clear_active(model_IEEE802_15_4.IRQ_NAME)
    assert Interrupts.Active_Interrupts[model_IEEE802_15_4.IRQ_NAME] is False
    SetupPeripheralServer.qemu.irq_set_qmp.reset_mock()
    # Set model_IEEE802_15_4.rx_frame_isr to an arbitrary value.
    model_IEEE802_15_4.rx_frame_isr = 20
    model_IEEE802_15_4.enable_rx_isr("an_interface_id")
    assert Interrupts.Active_Interrupts[model_IEEE802_15_4.IRQ_NAME] is True
    SetupPeripheralServer.qemu.irq_set_qmp.assert_called_once_with(
        model_IEEE802_15_4.rx_frame_isr
    )


class XfailException(Exception):
    pass


@pytest.mark.xfail(
    raises=XfailException,
    reason="'cls.frame_queue > 0' should be 'len(cls.frame_queue) > 0'",
)
def test_get_rx_frame_and_time_yields_TypeError():
    try:
        model_IEEE802_15_4.get_first_frame_and_time()
    except TypeError as ex:
        if (
            str(ex)
            == "'>' not supported between instances of 'collections.deque' and 'int'"
        ):
            raise XfailException from ex
        else:
            raise


class MockedDeque(deque):
    def __gt__(self, x):
        return len(self) > x


def test_patched_get_first_frame_and_time_yields_frame_and_time(
    model_IEEE802_15_4_recv_message,
):
    msg, msg_time = model_IEEE802_15_4_recv_message
    with mock.patch.object(
        model_IEEE802_15_4,
        "frame_queue",
        MockedDeque(model_IEEE802_15_4.frame_queue),
    ):
        assert model_IEEE802_15_4.get_first_frame_and_time() == (
            msg["frame"],
            msg_time,
        )


def test_patched_get_first_frame_yields_frame(model_IEEE802_15_4_recv_message):
    msg, msg_time = model_IEEE802_15_4_recv_message
    with mock.patch.object(
        model_IEEE802_15_4,
        "frame_queue",
        MockedDeque(model_IEEE802_15_4.frame_queue),
    ):
        assert model_IEEE802_15_4.get_first_frame() == msg["frame"]


def test_patched_get_first_frame_yields_None():
    model_IEEE802_15_4.frame_queue.clear()
    with mock.patch.object(
        model_IEEE802_15_4,
        "frame_queue",
        MockedDeque(model_IEEE802_15_4.frame_queue),
    ):
        assert model_IEEE802_15_4.get_first_frame() is None


def test_patched_get_first_frame_and_time_yields_None():
    model_IEEE802_15_4.frame_queue.clear()
    model_IEEE802_15_4.frame_time.clear()
    with mock.patch.object(
        model_IEEE802_15_4,
        "frame_queue",
        MockedDeque(model_IEEE802_15_4.frame_queue),
    ):
        assert model_IEEE802_15_4.get_first_frame_and_time() == (None, None)


def test_get_frame_info_yields_nonempty_frame_info(
    model_IEEE802_15_4_recv_message,
):
    msg, _ = model_IEEE802_15_4_recv_message
    assert model_IEEE802_15_4.get_frame_info() == (1, len(msg["frame"]))


def test_get_frame_info_yields_empty_frame_info(
    model_IEEE802_15_4_recv_message,
):
    model_IEEE802_15_4.frame_queue.clear()
    assert model_IEEE802_15_4.get_frame_info() == (0, 0)


def test_tx_frame_passes_frame_to_ioserver():
    ioserver = IOServer()
    ioserver.start()
    rx_frame_handler = mock.Mock()
    ioserver.register_topic(
        "Peripheral.IEEE802_15_4.tx_frame", rx_frame_handler
    )
    frame = "frame".encode()
    model_IEEE802_15_4.tx_frame("an_interface_id", frame)
    wait_assert(
        lambda: rx_frame_handler.assert_called_once_with(
            ioserver, {"frame": frame}
        )
    )
    ioserver.shutdown()
    fix_server_shutdown(ioserver.rx_socket, ioserver.tx_socket, 1000)
