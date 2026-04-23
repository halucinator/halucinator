import builtins
import logging
import operator
from collections import defaultdict, deque
from contextlib import contextmanager
from unittest import mock

import pytest
from peripheral_models_helpers import (
    SetupPeripheralServer,
    assert_,
    setup_ioserver_device,
    wait_assert,
)
from thread_propagate_exceptions import ThreadPropagateExceptions

from halucinator.peripheral_models.spi import SPIMessage, SPIPublisher


def packet(spi_id, n_pack):
    """
    Packets sent from/to SPIPublisher as SPIMessage.
    """
    return f"packet #{n_pack} for spi_id {spi_id}".encode()


N_SPI_IDS = 2
N_PACKS = 2

EXPECTED_INIT_RX_BUFFERS = {
    spi_id: deque(
        b for n_pack in range(N_PACKS) for b in packet(spi_id, n_pack)
    )
    for spi_id in range(N_SPI_IDS)
}


def handle_ioserver_data_(ioserver):
    """
    Sending data from IO server to SPIPublisher.
    """
    # There is no SPIPublisher method to clear rx_buffers.
    SPIPublisher.rx_buffers.clear()
    for n_pack in range(N_PACKS):
        for spi_id in range(N_SPI_IDS):
            ioserver.send_msg(
                "Peripheral.SPIPublisher.rx_data",
                SPIMessage(id=spi_id, chars=packet(spi_id, n_pack)),
            )
    wait_assert(
        lambda: assert_(
            operator.eq,
            (dict(SPIPublisher.rx_buffers), EXPECTED_INIT_RX_BUFFERS),
        )
    )
    # Note that the sent bytes are stored as int's in SPIPublisher.rx_buffers.
    assert all(
        all(type(b) == int for b in SPIPublisher.rx_buffers[spi_id])
        for spi_id in range(N_SPI_IDS)
    )


class XfailException(Exception):
    """
    Custom exception indicating xfailure.
    """

    pass


@pytest.fixture(scope="module", autouse=True)
def setup_peripheral_server():
    yield from SetupPeripheralServer.setup_peripheral_server()


@pytest.fixture(scope="module", autouse=True)
def setup_ioserver():
    yield from setup_ioserver_device()


@pytest.fixture()
def handle_ioserver_data(setup_ioserver):
    ioserver = setup_ioserver
    handle_ioserver_data_(ioserver)


def test_spi_publisher_rx_data_handles_sent_ioserver_data(
    handle_ioserver_data,
):
    # The tested assertion is in handle_ioserver_data_, which is called by the fixture.
    pass


def test_spi_publisher_write_data_handled_by_ioserver(setup_ioserver):
    ioserver = setup_ioserver
    spipublusher_write_handler = mock.Mock()
    ioserver.register_topic(
        "Peripheral.SPIPublisher.write", spipublusher_write_handler
    )
    for spi_id in range(N_SPI_IDS):
        for n_pack in range(N_PACKS):
            SPIPublisher.write(spi_id, packet(spi_id, n_pack))
    expected_handler_args_list = [
        mock.call(
            ioserver, SPIMessage(id=spi_id, chars=packet(spi_id, n_pack))
        )
        for spi_id in range(N_SPI_IDS)
        for n_pack in range(N_PACKS)
    ]
    wait_assert(
        lambda: assert_(
            operator.eq,
            (
                spipublusher_write_handler.call_args_list,
                expected_handler_args_list,
            ),
        )
    )


@pytest.mark.xfail(
    raises=XfailException, reason="BUG #1: name 'apply' is not defined"
)
# Parameterize test just enough to cover all the bug's locations.
@pytest.mark.parametrize(
    "spi_id, count, block",
    [(0, len(EXPECTED_INIT_RX_BUFFERS[0]) + 1, False), (1, 1, True),],
)
def test_spi_publisher_read_yields_apply_name_error(
    handle_ioserver_data, spi_id, count, block
):
    try:
        SPIPublisher.read(spi_id=spi_id, count=count, block=block)
    except NameError as ex:
        if str(ex) == "name 'apply' is not defined":
            raise XfailException from ex
        else:
            raise


@contextmanager
def apply_name_error_patch():
    """
    Contextually, restore python2's built-in apply function.
    """

    def apply(f, *args):
        return f(*args)

    assert not hasattr(builtins, "apply")
    builtins.apply = apply
    try:
        yield
    finally:
        del builtins.apply


@pytest.mark.xfail(
    raises=XfailException,
    reason="BUG #2: sequence item 0: expected str instance, int found",
)
@pytest.mark.parametrize(
    "spi_id, count, block",
    [(0, len(EXPECTED_INIT_RX_BUFFERS[0]) + 1, False), (1, 1, True),],
)
def test_spi_publisher_read_yields_type_error(
    handle_ioserver_data, spi_id, count, block
):
    try:
        with apply_name_error_patch():
            SPIPublisher.read(spi_id=spi_id, count=count, block=block)
    except TypeError as ex:
        # This is because we assume that SPIPublisher.rx_data handles SPIMessage.
        if str(ex) == "sequence item 0: expected str instance, int found":
            raise XfailException from ex
        else:
            raise


class TypeErrorPatch(deque):
    """
    Make popleft convert int to str.
    """

    def popleft(self):
        x = super().popleft()
        assert type(x) == int
        return chr(x)


@pytest.mark.parametrize(
    "spi_id, count, block",
    [(0, len(EXPECTED_INIT_RX_BUFFERS[0]) + 1, False), (1, 1, True),],
)
def test_patched_spi_publisher_read_popsleft_rx_buffers(
    setup_ioserver, spi_id, count, block
):
    ioserver = setup_ioserver
    # Since deque is built-in, it cannot be patched. Mocking it with TypeErrorPatch instead.
    with mock.patch.object(
        SPIPublisher, "rx_buffers", defaultdict(TypeErrorPatch),
    ):
        handle_ioserver_data_(ioserver)
        with apply_name_error_patch():
            popped_data = SPIPublisher.read(
                spi_id=spi_id, count=count, block=block
            )
            # The expected behavior of SPIPublisher.read
            assert popped_data == "".join(
                map(chr, list(EXPECTED_INIT_RX_BUFFERS[spi_id])[:count])
            )
            assert (
                list(SPIPublisher.rx_buffers[spi_id])
                == list(EXPECTED_INIT_RX_BUFFERS[spi_id])[count:]
            )


def test_spi_publisher_read_is_blocked(setup_ioserver):
    class SPIPublisherMockLogDebug:
        """
        Trace calls to logging.Logger.debug in SPIPublisher.read to demonstrate blocking.
        """

        logger_debug = mock.Mock()

        @classmethod
        def read(cls, spi_id, count, block):
            with mock.patch.object(logging.Logger, "debug", cls.logger_debug):
                SPIPublisher.read(spi_id, count, block)

    # Start SPIPublisher.read in a thread and observe it blocked.
    read_thread = ThreadPropagateExceptions(
        target=SPIPublisherMockLogDebug.read, args=(0, 1, True)
    )
    SPIPublisher.rx_buffers.clear()
    SPIPublisherMockLogDebug.logger_debug.reset_mock()
    read_thread.start()
    # One second is more than sufficient for SPIPublisher.read to pass its wait
    # loop, unless it's blocked.
    read_thread.join(timeout=1)
    # Check that SPIPublisher.read is still blocked, given that the two
    # Logger.debug calls in SPIPublisher.read surround the loop.
    assert read_thread.is_alive()
    SPIPublisherMockLogDebug.logger_debug.assert_called_once()
    # Send messages for SPIPublisher to read.
    ioserver = setup_ioserver
    handle_ioserver_data_(ioserver)
    # Ignore SPIPublisher.read bugs as they are irrelevant to this test.
    try:
        read_thread.join(timeout=1)
    except:
        pass
    # Logger.debug was called just after the SPIPublisher.read wait loop.
    assert (
        mock.call("Done Blocking: SPIPublisher.read")
        in SPIPublisherMockLogDebug.logger_debug.call_args_list
    )
    assert not read_thread.is_alive()
