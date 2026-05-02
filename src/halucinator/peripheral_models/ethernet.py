# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import binascii
import logging
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any, DefaultDict, Deque, Dict, Optional, Tuple, Union

from . import peripheral_server
# from peripheral_server import PeripheralServer, peripheral_model
from .interrupts import Interrupts

InterfaceId = Union[int, str]
log = logging.getLogger(__name__)
# log.setLevel(logging.DEBUG)


class EthernetInterface:

    def __init__(self, interface_id: InterfaceId, enabled: bool = True,
                 calc_crc: bool = True, irq_num: Optional[int] = None) -> None:
        self.interface_id: InterfaceId = interface_id
        self.rx_queue: Deque[bytes] = deque()
        self.frame_times: Deque[float] = deque()
        self.calc_crc: bool = calc_crc
        self.irq_num: Optional[int] = irq_num
        self.enabled: bool = enabled
        self.irq_enabled: bool = True

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def flush(self) -> None:
        self.rx_queue.clear()

    def disable_irq(self) -> None:
        self.irq_enabled = False

    def enable_irq_bp(self) -> None:
        Interrupts.clear_active_bp(self.irq_num)

    def _fire_interrupt_bp(self) -> None:
        if self.rx_queue and self.irq_num:
            Interrupts.set_active_bp(self.irq_num)

    def _fire_interrupt_qmp(self) -> None:
        if self.rx_queue and self.irq_num:
            log.debug("Sending Interupt for %s: %#x" %(self.interface_id, self.irq_num))
            Interrupts.set_active_qmp(self.irq_num)

    def buffer_frame_qmp(self, frame: bytes) -> None:
        '''
        This method buffer the frame so it can be read into the firmware
        later using the get_frame method
        '''
        if self.enabled:
            self.rx_queue.append(frame)
            self.frame_times.append(time.time())
            log.info("Adding Frame to: %s" % self.interface_id)
            self._fire_interrupt_qmp()
        else:
            return

    def get_frame(self, get_time: bool = False) -> Union[Optional[bytes], Tuple[Optional[bytes], Optional[float]]]:
        frame: Optional[bytes] = None
        rx_time: Optional[float] = None

        if self.rx_queue:
            frame = self.rx_queue.popleft()
            rx_time = self.frame_times.popleft()

        if get_time:
            return frame, rx_time
        else:
            return frame

    def get_frame_info(self) -> Tuple[int, int]:
        '''
            Returns the number of frames in the Queue and number of
            len of first frame
        '''
        if self.rx_queue:
            return len(self.rx_queue), len(self.rx_queue[0])
        return 0, 0


# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class EthernetModel(object):

    interfaces: Dict[InterfaceId, EthernetInterface] = dict()
    frame_queues: DefaultDict[InterfaceId, Deque[bytes]] = defaultdict(deque)   # interface_id -> deque of frames
    frame_times: DefaultDict[InterfaceId, Deque[float]] = defaultdict(deque)    # interface_id -> deque of timestamps
    calc_crc: bool = True
    rx_frame_isr: Optional[int] = None
    rx_isr_enabled: bool = False

    IRQ_SOURCE: str = "Ethernet_RX_Frame"

    @classmethod
    def add_interface(cls, interface_id: InterfaceId, enabled: bool = True, calc_crc: bool = True, irq_num: Optional[int] = None) -> None:
        '''
            Used to add an interface to the model.

            interface_id:   The id used for the interface
            enable:         Interface is enabled
            calc_crc:       Should calculate and append CRC to sent frames
                            (used if HW would normally do this)
            irq_num:        The irq number to trigger on received frames for this
                            interfaces
        '''
        interface = EthernetInterface(interface_id, enabled=True, calc_crc=calc_crc,
                                       irq_num=irq_num)
        cls.interfaces[interface_id] = interface

    @classmethod
    def enable_rx_isr_bp(cls, interface_id: InterfaceId) -> None:
        if interface_id in cls.interfaces:
            cls.interfaces[interface_id].enable_irq_bp()

    @classmethod
    def disable_rx_isr_bp(cls, interface_id: InterfaceId) -> None:
        if interface_id in cls.interfaces:
            cls.interfaces[interface_id].disable_irq()

    @classmethod
    def enable_rx_isr(cls, interface_id: InterfaceId) -> None:
        """Enable interrupt-driven rx notification; fire IRQ if frames already queued."""
        cls.rx_isr_enabled = True
        if cls.rx_frame_isr is not None and cls.frame_queues[interface_id]:
            Interrupts.trigger_interrupt(cls.rx_frame_isr, source=cls.IRQ_SOURCE)

    @classmethod
    def disable_rx_isr(cls, interface_id: InterfaceId) -> None:
        """Disable interrupt-driven rx notification."""
        cls.rx_isr_enabled = False

    @classmethod
    def enable(cls, interface_id: InterfaceId) -> None:
        cls.interfaces[interface_id].enable()

    @classmethod
    def flush(cls, interface_id: InterfaceId) -> None:
        cls.interfaces[interface_id].flush()

    @classmethod
    def disable(cls, interface_id: InterfaceId) -> None:
        cls.interfaces[interface_id].disable()

    @classmethod
    @peripheral_server.tx_msg
    def tx_frame(cls, interface_id: InterfaceId, frame: bytes) -> Dict[str, Any]:
        '''
            Creates the message that Peripheral.tx_msga will send on this
            event
        '''
        # TODO append CRC if needed for the interface
        print("Sending Frame (%i): " % len(frame), binascii.hexlify(frame))
        # print ""
        msg: Dict[str, Any] = {'interface_id': interface_id, 'frame': frame}
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def rx_frame(cls, msg: Dict[str, Any]) -> None:
        '''
            Processes reception of this type of message from
            PeripheralServer.rx_msg
        '''
        interface_id: InterfaceId = msg['interface_id']
        log.info("Adding Frame to: %s" % interface_id)
        frame: bytes = msg['frame']
        rx_time = time.time()
        cls.frame_queues[interface_id].append(frame)
        cls.frame_times[interface_id].append(rx_time)
        if interface_id in cls.interfaces:
            cls.interfaces[interface_id].buffer_frame_qmp(frame)
        if cls.rx_isr_enabled and cls.rx_frame_isr is not None:
            Interrupts.trigger_interrupt(cls.rx_frame_isr, source=cls.IRQ_SOURCE)

    @classmethod
    def get_rx_frame(
        cls, interface_id: InterfaceId, get_time: bool = False
    ) -> Union[Optional[bytes], Tuple[Optional[bytes], Optional[float]]]:
        log.info("Getting RX frame from: %s" % str(interface_id))
        if not cls.frame_queues[interface_id]:
            return (None, None) if get_time else None
        frame = cls.frame_queues[interface_id].popleft()
        rx_time = cls.frame_times[interface_id].popleft() if cls.frame_times[interface_id] else None
        return (frame, rx_time) if get_time else frame

    @classmethod
    def get_rx_frame_only(cls, interface_id: InterfaceId) -> Optional[bytes]:
        """Pop and return the oldest frame (no timestamp). Returns None if empty."""
        if not cls.frame_queues[interface_id]:
            return None
        frame = cls.frame_queues[interface_id].popleft()
        if cls.frame_times[interface_id]:
            cls.frame_times[interface_id].popleft()
        return frame

    @classmethod
    def get_rx_frame_and_time(
        cls, interface_id: InterfaceId
    ) -> Tuple[Optional[bytes], Optional[float]]:
        """Return (frame, rx_time) tuple, or (None, None) if empty."""
        if not cls.frame_queues[interface_id]:
            return None, None
        frame = cls.frame_queues[interface_id].popleft()
        rx_time = cls.frame_times[interface_id].popleft() if cls.frame_times[interface_id] else None
        return frame, rx_time

    @classmethod
    def get_frame_info(cls, interface_id: InterfaceId) -> Tuple[int, int]:
        """Return (num_frames, size_of_first_frame) or (0, 0) if empty."""
        q = cls.frame_queues[interface_id]
        if q:
            return len(q), len(q[0])
        return 0, 0


@dataclass
class EthernetMessage:
    """Typed message for Peripheral.EthernetModel topics."""
    interface_id: str
    frame: bytes

    def __getitem__(self, key: str) -> Any:
        return asdict(self)[key]
