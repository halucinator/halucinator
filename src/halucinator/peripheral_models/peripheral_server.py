# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Peripheral Server that enables external devices to send and receive events
from HALucinator over ZMQ
"""
from __future__ import annotations

import logging
import queue
import threading
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type, TypeVar, cast

import yaml
import zmq

log = logging.getLogger(__name__)

# pylint: disable=global-statement

__RX_HANDLERS__ = {}
__RX_CONTEXT__ = zmq.Context()
__TX_CONTEXT__ = zmq.Context()
__STOP_SERVER = False
__RX_SOCKET__: Optional[zmq.Socket] = None
__TX_SOCKET__: Optional[zmq.Socket] = None

# Lowercase aliases used by test helpers
__rx_socket__: Optional[zmq.Socket] = None  # updated alongside __RX_SOCKET__
__tx_socket__: Optional[zmq.Socket] = None  # updated alongside __TX_SOCKET__

__PROCESS = None
__QEMU = None

# Deferred IRQ delivery (issue #31). IRQs requested from a context where
# the vCPU is mid-execution — a forwarded-MMIO hw_read/hw_write handler —
# cannot be injected inline: the QMP path deadlocks on the QEMU global
# lock and the GDB path is refused ("target is running"). The worker
# thread below drains a queue and performs the actual inject once the
# originating MMIO access has returned and the vCPU is idle again.
__IRQ_QUEUE: "queue.Queue[Any]" = queue.Queue()
__IRQ_WORKER: Optional[threading.Thread] = None
__IRQ_WORKER_STOP = object()  # sentinel pushed onto the queue to end the loop

OUTPUT_DIRECTORY: Optional[str] = None


Publisher = TypeVar("Publisher")


def peripheral_model(cls: Type[Publisher]) -> Type[Publisher]:
    """
    Decorator which registers classes as peripheral models
    """
    methods = [
        getattr(cls, x) for x in dir(cls) if hasattr(getattr(cls, x), "is_rx_handler")
    ]
    for method in methods:
        key = f"Peripheral.{cls.__name__}.{method.__name__}"
        log.info("Adding method: %s", key)
        __RX_HANDLERS__[key] = (cls, method)
        if __RX_SOCKET__ is not None:
            log.info("Subscribing to: %s", key)
            __RX_SOCKET__.setsockopt(zmq.SUBSCRIBE, bytes(key))

    return cls


CallableVar = TypeVar("CallableVar", bound=Callable[..., Any])


def tx_msg(funct: CallableVar) -> CallableVar:
    """
    This is a decorator that sends output of the wrapped function as
    a tagged msg.  The tag is the class_name.func_name
    """

    @wraps(funct)
    def tx_msg_decorator(model_cls: Any, *args: Any) -> None:
        """
        Sends a message using the class.funct as topic
        data is a yaml encoded of the calling model_cls.funct
        """
        data = funct(model_cls, *args)
        topic = f"Peripheral.{model_cls.__name__}.{funct.__name__}"
        msg = encode_zmq_msg(topic, data)
        log.info("Sending: %s", msg)
        __TX_SOCKET__.send_string(msg)

    return cast(CallableVar, tx_msg_decorator)


def reg_rx_handler(funct: CallableVar) -> CallableVar:
    """
    This is a decorator that registers a function to handle a specific
    type of message
    """
    funct.is_rx_handler = True  # type: ignore
    return funct


def encode_zmq_msg(topic: str, msg: Any) -> str:
    """
    Encodes a message sent over a zmq

    :param topic: Str of topic to send
    :param msg:  (Data that can be dump to yaml)
    """
    import dataclasses
    if dataclasses.is_dataclass(msg) and not isinstance(msg, type):
        msg = dataclasses.asdict(msg)
    data_yaml = yaml.safe_dump(msg)
    return f"{topic} {data_yaml}"


def decode_zmq_msg(msg: str) -> Tuple[str, Any]:
    """
    Decodes a message sent over a zmq socket
    returns (topic, message)
    """
    topic, encoded_msg = str(msg).split(" ", 1)
    decoded_msg = yaml.safe_load(encoded_msg)
    return (topic, decoded_msg)


def start(rx_port: int = 5555, tx_port: int = 5556, qemu: Any = None) -> None:
    """
    Initializes zmq sockets
    """
    global __RX_SOCKET__
    global __TX_SOCKET__
    global __rx_socket__
    global __tx_socket__
    global __QEMU
    global OUTPUT_DIRECTORY

    OUTPUT_DIRECTORY = qemu.avatar.output_directory
    __QEMU = qemu
    log.info("Starting Peripheral Server, In port %i, outport %i", rx_port, tx_port)
    # Setup subscriber
    io2hal_pipe = f"ipc:///tmp/IoServer2Halucinator{rx_port}"
    __RX_SOCKET__ = __RX_CONTEXT__.socket(zmq.SUB)
    __rx_socket__ = __RX_SOCKET__
    __RX_SOCKET__.bind(io2hal_pipe)
    log.debug("Bound to %s", str(io2hal_pipe))

    for topic in list(__RX_HANDLERS__.keys()):
        log.info("Subscribing to: %s", topic)
        __RX_SOCKET__.setsockopt_string(zmq.SUBSCRIBE, topic)

    # Setup Publisher
    hal2io_pipe = f"ipc:///tmp/Halucinator2IoServer{tx_port}"
    __TX_SOCKET__ = __TX_CONTEXT__.socket(zmq.PUB)
    __tx_socket__ = __TX_SOCKET__
    __TX_SOCKET__.bind(hal2io_pipe)
    log.debug("Bound to %s", str(hal2io_pipe))

    start_irq_worker()

    # __process = Process(target=run_server).start()


def _irq_worker_loop() -> None:
    """Drain __IRQ_QUEUE and perform the actual (potentially blocking)
    inject from a thread that does not hold the QEMU global lock."""
    while True:
        item = __IRQ_QUEUE.get()
        try:
            if item is __IRQ_WORKER_STOP:
                return
            try:
                inject_irq(item)
            except Exception:  # pylint: disable=broad-except
                # Never let a single failed delivery kill the worker — the
                # next queued IRQ should still get a chance.
                log.exception("Deferred inject_irq(%s) failed", item)
        finally:
            __IRQ_QUEUE.task_done()


def start_irq_worker() -> None:
    """Start the deferred-IRQ delivery thread (idempotent)."""
    global __IRQ_WORKER
    if __IRQ_WORKER is not None and __IRQ_WORKER.is_alive():
        return
    __IRQ_WORKER = threading.Thread(
        target=_irq_worker_loop, name="hal-irq-delivery", daemon=True
    )
    __IRQ_WORKER.start()
    log.info("Started deferred IRQ delivery thread")


def stop_irq_worker() -> None:
    """Signal the deferred-IRQ delivery thread to exit."""
    global __IRQ_WORKER
    worker = __IRQ_WORKER
    if worker is not None and worker.is_alive():
        __IRQ_QUEUE.put(__IRQ_WORKER_STOP)
        worker.join(timeout=2.0)
    __IRQ_WORKER = None


def inject_irq_deferred(irq_num: int = 1) -> None:
    """Queue *irq_num* for asynchronous injection on the IRQ worker thread.

    SAFE to call from any context, including a peripheral hw_read/hw_write
    handler (forwarded-MMIO context) where the vCPU holds the QEMU global
    lock and a GDB ``cont`` is outstanding. Injecting inline from there
    deadlocks (QMP) or is rejected (GDB "target is running") — see issue
    #31. Deferring lets the originating MMIO access return first, so the
    inject runs while the vCPU is idle (e.g. parked in WFI) and is woken
    cleanly.

    Falls back to immediate injection when the worker thread isn't
    running (early init / unit tests), which is correct from the clean
    contexts those callers use.
    """
    if __QEMU is None:
        return
    if __IRQ_WORKER is None or not __IRQ_WORKER.is_alive():
        inject_irq(irq_num)
        return
    __IRQ_QUEUE.put(int(irq_num))


def trigger_interrupt(irq_num: int, source: Optional[str] = None) -> None:
    """Trigger an interrupt by number on the running backend.

    Injects immediately on the calling thread. Safe from clean contexts
    (external devices, the ZMQ server thread). To assert an IRQ from a
    forwarded-MMIO hw_read/hw_write handler, use ``inject_irq_deferred``
    instead (issue #31).
    """
    log.info("Triggering interrupt %s (source=%s)", irq_num, source)
    inject_irq(irq_num)


def inject_irq(irq_num: int = 1) -> None:
    """Inject *irq_num* on the running backend.

    Prefers the modern HalBackend.inject_irq path (which routes
    through the configured IrqController for non-Cortex-M arches).
    Falls back to the legacy avatar2 QemuTarget.irq_set_qmp method
    when the registered backend doesn't expose inject_irq — that
    keeps the existing avatar2 + cortex-m flow working unchanged.
    """
    if __QEMU is None:
        return
    inject = getattr(__QEMU, "inject_irq", None)
    if callable(inject):
        inject(irq_num)
        return
    # Legacy avatar2 path: bare QemuTarget with the halucinator-irq
    # qom device.
    legacy = getattr(__QEMU, "irq_set_qmp", None)
    if callable(legacy):
        legacy(irq_num)


def irq_set_qmp(irq_num: int = 1) -> None:
    """Deprecated alias for inject_irq().

    Kept so external_devices/* call sites that imported this name
    keep working. Will be removed in a future release.
    """
    inject_irq(irq_num)


def irq_clear_qmp(irq_num: int = 1) -> None:
    """Clear irq_num via QMP. No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_clear_qmp(irq_num)


def irq_enable_qmp(irq_num: int = 1) -> None:
    """Enable irq_num via QMP. No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_enable_qmp(irq_num)


def irq_disable_qmp(irq_num: int = 1) -> None:
    """Disable irq_num via QMP. No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_disable_qmp(irq_num)


def irq_set_bp(irq_num: int = 1) -> None:
    """Set irq_num via GDB (safe from BP handlers). No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_set_bp(irq_num)


def irq_clear_bp(irq_num: int) -> None:
    """Clear irq_num via GDB (safe from BP handlers). No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_clear_bp(irq_num)


def irq_enable_bp(irq_num: int) -> None:
    """Enable irq_num via GDB (safe from BP handlers). No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_enable_bp(irq_num)


def irq_disable_bp(irq_num: int) -> None:
    """Disable irq_num via GDB (safe from BP handlers). No-op if QEMU not connected."""
    if __QEMU is not None:
        __QEMU.irq_disable_bp(irq_num)


# def irq_set(irq_num=1, cpu=0):
#     global __QEMU
#     __QEMU.irq_set(irq_num, cpu)

# def irq_clear(self, irq_num=1, cpu=0):
#     global __QEMU
#     __QEMU.irq_clear(irq_num, cpu)

# def irq_pulse(self, irq_num=1, cpu=0):
#     global __QEMU
#     __QEMU.irq_pulse(irq_num, cpu)


def run_server() -> None:
    """
    This the main loop for the peripheral server.
    """
    global __STOP_SERVER  # pylint: disable=global-statement

    __STOP_SERVER = False
    __RX_SOCKET__.setsockopt(zmq.SUBSCRIBE, b"")  # pylint: disable=no-member

    poller = zmq.Poller()
    poller.register(__RX_SOCKET__, zmq.POLLIN)
    while not __STOP_SERVER:
        socks = dict(poller.poll(100))
        if __RX_SOCKET__ in socks and socks[__RX_SOCKET__] == zmq.POLLIN:
            string = __RX_SOCKET__.recv_string()
            topic, msg = decode_zmq_msg(string)
            log.info("Got message: Topic %s  Msg: %s", str(topic), str(msg))
            print(f"Got message: Topic {topic}  Msg: {msg}")
            if topic.startswith("Peripheral"):
                if topic in __RX_HANDLERS__:
                    _, method = __RX_HANDLERS__[topic]
                    method(msg)
                else:
                    log.error("Unhandled peripheral message type received: %s", topic)

            elif topic.startswith("Interrupt.Trigger"):
                log.info("Triggering Interrupt %s", msg["num"])
                inject_irq(msg["num"])
            elif topic.startswith("Interrupt.Base"):
                log.info("Setting Vector Base Addr %s" % msg["num"])
                __QEMU.set_vector_table_base(msg["base"])
            else:
                log.error("Unhandled topic received: %s", topic)
    log.info("Peripheral Server Shutdown Normally")


def stop() -> None:
    """
    Stop the Peripheral Server
    """
    global __STOP_SERVER  # pylint: disable=global-statement
    __STOP_SERVER = True
    stop_irq_worker()
