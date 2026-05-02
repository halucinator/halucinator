# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import binascii
import logging
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict, Optional, Tuple, Union

from . import peripheral_server
# from peripheral_server import PeripheralServer, peripheral_model
from .interrupts import Interrupts

# See a comment in ethernet.py for an analogous construct there
InterfaceId = Union[int, str]
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class IEEE802_15_4(object):

    IRQ_NAME: str = '802_15_4_RX_Frame'
    frame_queue: Deque[bytes] = deque()
    calc_crc: bool = True
    rx_frame_isr: Optional[int] = None
    rx_isr_enabled: bool = False
    frame_time: Deque[float] = deque()  # Used to record reception time

    @classmethod
    def enable_rx_isr(cls, interface_id: InterfaceId) -> None:
        cls.rx_isr_enabled = True
        if cls.frame_queue and cls.rx_frame_isr is not None:
            Interrupts.trigger_interrupt(cls.rx_frame_isr, cls.IRQ_NAME)

    @classmethod
    def disable_rx_isr(self, interface_id: InterfaceId) -> None:
        IEEE802_15_4.rx_isr_enabled = False

    @classmethod
    @peripheral_server.tx_msg
    def tx_frame(cls, interface_id: InterfaceId, frame: bytes) -> Dict[str, Any]:
        '''
            Creates the message that Peripheral.tx_msga will send on this
            event
        '''
        print("Sending Frame (%i): " % len(frame), binascii.hexlify(frame))
        msg = {'frame': frame}
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def rx_frame(cls, msg: Dict[str, Any]) -> None:
        '''
            Processes reception of this type of message from
            PeripheralServer.rx_frame
        '''
        frame = msg['frame']
        log.info("Received Frame: %s" % binascii.hexlify(frame))

        cls.frame_queue.append(frame)
        cls.frame_time.append(time.time())
        if cls.rx_frame_isr is not None and cls.rx_isr_enabled:
            Interrupts.trigger_interrupt(cls.rx_frame_isr,  cls.IRQ_NAME)

    @classmethod
    def get_first_frame(
        cls, get_time: bool = False
    ) -> Union[Optional[bytes], Tuple[Optional[bytes], Optional[float]]]:
        frame: Optional[bytes] = None
        rx_time: Optional[float] = None
        log.info("Checking for frame")
        if len(cls.frame_queue) > 0:
            log.info("Returning frame")
            frame = cls.frame_queue.popleft()
            rx_time = cls.frame_time.popleft()

        if get_time:
            return frame, rx_time
        else:
            return frame

    @classmethod
    def get_first_frame_and_time(
        cls,
    ) -> Tuple[Optional[bytes], Optional[float]]:
        """Return (frame, rx_time) or (None, None) if queue is empty."""
        return cls.get_first_frame(get_time=True)

    @classmethod
    def has_frame(cls) -> bool:
        return len(cls.frame_queue) > 0

    @classmethod
    def get_frame_info(cls) -> Tuple[int, int]:
        '''
            return number of frames and length of first frame
        '''
        queue = cls.frame_queue
        if queue:
            return len(queue), len(queue[0])
        return 0, 0


@dataclass
class IEEE802_15_4Message:
    """Typed message for Peripheral.IEEE802_15_4 topics."""
    frame: bytes

    def __getitem__(self, key: str) -> Any:
        return asdict(self)[key]
