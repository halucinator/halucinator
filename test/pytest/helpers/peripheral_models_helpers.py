import gc
import logging
import operator
import sys
import time
import traceback
from collections import defaultdict, deque, namedtuple
from io import StringIO
from time import sleep
from unittest import mock

import pytest
import zmq
from thread_propagate_exceptions import ThreadPropagateExceptions

from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.vn8200xp import VN8200XP
from halucinator.peripheral_models import peripheral_server
from halucinator.peripheral_models.uart import UARTPublisher
from halucinator.qemu_targets.armv7m_qemu import ARMv7mQemuTarget

# Number of devices
N_MESSAGES = 2
# Number of messages
N_IDS = 2

PS_RX_PORT = 5555
PS_TX_PORT = 5556

logger = logging.getLogger(__name__)


def join_timeout(proc_or_thread, timeout=20):
    """
    Waiting for either process or thread with the default infinite timeout may
    lead to test hangups that are difficult to diagnose. Normally, we expect joining
    within a reasonable time, implemented in this function.
    """
    proc_or_thread.join(timeout)
    assert not proc_or_thread.is_alive()


def print_test(device_cls):
    class PrintTest(device_cls):
        """
        Store written mesages for checking assertions
        """

        def __init__(self, ioserver):
            super().__init__(ioserver)
            self.written_messages = defaultdict(list)
            self.ipython_embed_mock = (
                mock.Mock() if device_cls == VN8200XP else None
            )

        def write_handler(self, ioserver, msg):
            if self.ipython_embed_mock is None:
                super().write_handler(ioserver, msg)
            else:
                # Mocking IPython.embed() as a no-op in VN8200XP.write_handler
                # is adequate for peripheral models testing. Leaving it as is
                # results in exception "OSError: pytest: reading from stdin
                # while output is captured!...". Handling the exception with a
                # no-op seems adequate, and it works in scope of
                # test/pytest/peripheral_models testing. However, it still
                # results in failures/timeouts when running all the tests in
                # test/pytest. So we just mocking IPython.embed() as a no-op
                # here.
                with mock.patch("IPython.embed", self.ipython_embed_mock):
                    super().write_handler(ioserver, msg)
            self.written_messages[msg["id"]].append(msg["chars"])

    return PrintTest


IOServerDevice = namedtuple("IOServerDevice", ["io_server", "device"])


def setup_ioserver_device(
    device_cls=None,
    post_start_delay=0.1,
    io_rx_port=PS_TX_PORT,
    io_tx_port=PS_RX_PORT,
):
    """
    Set up IO server, and optionally create a device using it. Used as a setup fixture.

    device_cls: device class, if any.

    poststart_delay: a delay after starting the server, and before
    sending/receiving messages. The value reflects networking delays in setting
    up the sockets and the listening loop. The value is empirically selected.
    """
    UARTPublisher.rx_buffers.clear()
    try:
        io_server = IOServer(rx_port=io_rx_port, tx_port=io_tx_port)
    except zmq.error.ZMQError as ex:
        if str(ex) == "Address already in use":
            pytest.xfail("IO server shutdown does not close sockets")
        else:
            raise
    io_server.start()
    if device_cls:
        device = print_test(device_cls)(io_server)
    sleep(post_start_delay)
    logger.debug("Started IO server")
    if device_cls:
        yield IOServerDevice(io_server, device)
    else:
        yield io_server
    io_server.shutdown()
    # The IOServer.shutdown method does not close the sockets, which is a bug
    # exemplified by
    # peripheral_models/test_servers_setup.py::test_ioserver_shutdown_bug. The
    # provisional fix fix_server_shutdown() is applied below. It needs to be
    # removed once IOServer.shutdown is fixed.
    fix_server_shutdown(io_server.rx_socket, io_server.tx_socket, 1000)
    # Give the IOServer thread a moment to actually return from its recv
    # loop — under full-suite scheduler load the transition from
    # "shutdown requested" to "thread exited" isn't instantaneous.
    io_server.join(timeout=2.0)
    assert not io_server.is_alive()
    logger.debug("Shutdown IO server")


class PeripheralServerThread(ThreadPropagateExceptions):
    """
    This class defines a thread with a target that mocks some aspects of
    peripheral_server.run_server.
    """

    def __init__(self):
        self.xfail_msgs = []
        super().__init__(target=self.run_server_mock)

    def run_server_mock(self):
        """
        This instrumentation of peripheral_server.run_server accounts for expected failures.
        """
        try:
            peripheral_server.run_server()
        # Account for the "KeyError: 'num'" bug in peripheral_server.run_server.
        except KeyError as e:
            if str(e) != "'num'":
                raise
            exception_traceback = sys.exc_info()[2]
            out = StringIO("")
            traceback.print_tb(exception_traceback, file=out)
            expected_strs = (
                "peripheral_models/peripheral_server.py",
                "in run_server",
                'log.info("Setting Vector Base Addr %s" % msg["num"])',
            )
            check = all(s in out.getvalue() for s in expected_strs)
            if not check:
                raise
            self.xfail_msgs.append(
                "peripheral_models/peripheral_server.py: run_server: "
                'log.info("Setting Vector Base Addr %s" % msg["num"]): KeyError: "num"'
            )
        except:
            raise


class SetupPeripheralServer:
    """
    Start/stop peripheral server
    """

    # The peripheral_server.run_server thread
    peripheral_server_thread = None
    # cls.setup_peripheral_server (see below) starts peripheral_server with a mocked
    # qemu.
    qemu = mock.Mock(
        avatar=mock.Mock(output_directory=None),
        irq_set_qmp=mock.Mock(),
        set_vector_table_base=mock.Mock(),
    )
    # This is to satisfy isinstance assertions in peripheral_server.py
    qemu.__class__ = ARMv7mQemuTarget

    @classmethod
    def setup_peripheral_server(
        cls,
        post_start_delay=0.2,
        run_server=True,
        rx_port=PS_RX_PORT,
        tx_port=PS_TX_PORT,
    ):
        """
        Set up peripheral server server. Used as a setup fixture.

        poststart_delay: a delay after starting the server, and before
        sending/receiving messages. The value reflects networking delays in setting
        up the sockets and the listening loop. The value is empirically selected.
        """
        peripheral_server.start(
            rx_port=rx_port, tx_port=tx_port, qemu=cls.qemu,
        )
        # The peripheral_server.run_server thread
        cls.peripheral_server_thread = PeripheralServerThread()
        if run_server:
            cls.peripheral_server_thread.start()
        sleep(post_start_delay)
        logger.debug("Started peripheral server")
        yield
        if run_server:
            peripheral_server.stop()
        # The peripheral_server.stop method does not close the sockets, which is
        # a bug exemplified by
        # peripheral_models/test_servers_setup.py::test_peripheral_server_stop_bug. The
        # provisional fix fix_server_shutdown() is applied below. It needs to be
        # removed once peripheral_server.stop is fixed.
        fix_server_shutdown(
            peripheral_server.__rx_socket__,
            peripheral_server.__tx_socket__,
            500,
        )
        assert not cls.peripheral_server_thread.is_alive()
        cls.peripheral_server_thread.check_exception()
        logger.debug("Stopped peripheral server")


def fix_server_shutdown(rx_socket, tx_socket, poller_timeout_ms):
    """
    Provisional fix for IOServer.shutdown and peripheral_server.stop
    """
    # Let the poller finish.
    sleep(poller_timeout_ms / 1000)
    # close sockets
    rx_socket.close()
    tx_socket.close()
    # In addition, explicit garbage collection is needed to force closing
    # the underlying sockets in some cases (see
    # https://pyzmq.readthedocs.io/en/latest/api/zmq.html#zmq.Socket.close).
    rx_socket = None
    tx_socket = None
    gc.collect()


def message_from_uart(id, msg_num):
    return f"message #{msg_num} from uart #{id}"


EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS = {
    uart_id: deque(
        ch
        for msg_num in range(N_MESSAGES)
        for ch in message_from_uart(uart_id, msg_num)
    )
    for uart_id in range(N_IDS)
}


def device_send_to_UARTPublisher(device):
    for msg_num in range(N_MESSAGES):
        for id in range(N_IDS):
            device.send_data(id, message_from_uart(id, msg_num))


def do_test_send_to_UARTPublisher(device, post_send_delay):
    """
    Check receiving multiple messages from multiple uart devices

    post_send_delay: a delay after sending all the messages and before checking
    their correct reception. It is nessesary for the receiver's poller to catch
    up. In general, the transmission delay is proportional to (N_IDS *
    N_MESSAGES).
    """

    # initially, there are no received messages
    assert (
        len(UARTPublisher.rx_buffers) == 0
    ), f"len(UARTPublisher.rx_buffers): {len(UARTPublisher.rx_buffers)}"
    # send data
    device_send_to_UARTPublisher(device)
    # verify reception
    wait_assert(
        lambda: assert_(
            operator.eq,
            (
                dict(UARTPublisher.rx_buffers),
                EXPECTED_INIT_UARTPUBLISHER_RX_BUFFERS,
            ),
        ),
        post_send_delay,
        1,
    )


def do_test_receive_from_UARTPublisher(device, post_send_delay):
    """
    Check receiving multiple messages to multiple uart devices
    """

    def message_to_uart(id, msg_num):
        return f"message #{msg_num} to uart #{id}".encode("latin-1")

    # initially, there are no received messages
    assert (
        len(device.written_messages) == 0
    ), f"len(device.written_messages): {len(device.written_messages)}"
    # send data
    for msg_num in range(N_MESSAGES):
        for id in range(N_IDS):
            UARTPublisher.write(id, message_to_uart(id, msg_num))
    sleep(post_send_delay)
    # verify reception
    sent_ids = set(range(N_IDS))
    recv_ids = set(device.written_messages.keys())
    assert recv_ids == sent_ids, f"recv_ids: {recv_ids}, sent_ids: {sent_ids}"
    for id in recv_ids:
        assert (
            len(device.written_messages[id]) == N_MESSAGES
        ), f"len(device.written_messages[id]): {len(device.written_messages[id])}, N_MESSAGES: {N_MESSAGES}"
        for msg_num in range(N_MESSAGES):
            sent_msg = device.written_messages[id][msg_num]
            recv_msg = message_to_uart(id, msg_num)
            assert (
                recv_msg == sent_msg
            ), f"recv_msg: {recv_msg}, sent_msg: {sent_msg}"
    if device.ipython_embed_mock:
        assert device.ipython_embed_mock.call_count == N_IDS * N_MESSAGES


def assert_(fct, args):
    """
    Used to construct assertion statements for wait_assert calls.
    """

    def error_msg(fct, args):
        args_str = "\n" + "\n".join(
            [f"arg #{i}: {arg}" for i, arg in enumerate(args)]
        )
        return f"Calling {str(fct)} with these {len(args)} args:{args_str}"

    assert fct(*args), error_msg(fct, args)


def wait_assert(assertion, delay_incr=0.1, num_tries=10):
    """
    Check on assertion after a number of delay increments. Used to accomodate fluctuating network delays.

    assertion - a function (usually a lambda expression) representing an assertion statement.
    delay - delay increment before checking the assertion.
    num_tries - the maximum number of tries before reporting the failed assertion.
    """
    assert num_tries
    for i in range(num_tries):
        time.sleep(delay_incr)
        try:
            assertion()
            break
        except AssertionError:
            if i == num_tries - 1:
                raise
            else:
                pass
