# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import binascii
import logging
import time
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Optional, Tuple, Union

from typing import TypedDict

from . import peripheral_server
# from peripheral_server import PeripheralServer, peripheral_model
from .interrupts import Interrupts

InterfaceId = Union[int, str]
log = logging.getLogger(__name__)
# log.setLevel(logging.DEBUG)


class EthernetMessage(TypedDict):
    interface_id: InterfaceId
    frame: bytes


class EthernetInterface:

    def __init__(self, interface_id: InterfaceId, enabled: bool = True,
                 calc_crc: bool = True, irq_num: Optional[int] = None) -> None:
        self.interface_id = interface_id
        self.rx_queue = deque()
        self.frame_times = deque()
        self.calc_crc = calc_crc
        self.irq_num = irq_num
        self.enabled = enabled

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
        frame = None
        rx_time = None
        
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

    frame_queues: DefaultDict[InterfaceId, Deque[bytes]] = defaultdict(deque)
    calc_crc = True
    rx_frame_isr: Optional[int] = None
    rx_isr_enabled = False
    frame_times: DefaultDict[InterfaceId, Deque[float]] = defaultdict(
        deque
    )  # Used to record reception time
    interfaces = dict()

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
    def enable_rx_isr(cls, interface_id: InterfaceId) -> None:
        cls.rx_isr_enabled = True
        if cls.frame_queues[interface_id] and cls.rx_frame_isr is not None:
            Interrupts.trigger_interrupt(cls.rx_frame_isr, "Ethernet_RX_Frame")

    @classmethod
    def enable_rx_isr_bp(cls, interface_id: InterfaceId) -> None:
        if interface_id in cls.interfaces:
            cls.interfaces[interface_id].enable_irq_bp()

    @classmethod
    def disable_rx_isr(self, interface_id: InterfaceId) -> None:
        EthernetModel.rx_isr_enabled = False

    @classmethod
    def disable_rx_isr_bp(cls, interface_id: InterfaceId) -> None:
        if interface_id in cls.interfaces:
            cls.interfaces[interface_id].disable_irq()

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
    def tx_frame(cls, interface_id: InterfaceId, frame: bytes) -> EthernetMessage:
        """
            Creates the message that Peripheral.tx_msga will send on this
            event
        """
        print("Sending Frame (%i): " % len(frame), binascii.hexlify(frame))
        # print ""
        msg: EthernetMessage = {"interface_id": interface_id, "frame": frame}
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def rx_frame(cls, msg: EthernetMessage) -> None:
        """
            Processes reception of this type of message from
            PeripheralServer.rx_msg
        """
        interface_id = msg["interface_id"]
        log.info("Adding Frame to: %s" % interface_id)
        frame = msg["frame"]
        cls.frame_queues[interface_id].append(frame)
        cls.frame_times[interface_id].append(time.time())
        log.info("Adding Frame to: %s" % interface_id)
        if cls.rx_frame_isr is not None and cls.rx_isr_enabled:
            Interrupts.trigger_interrupt(cls.rx_frame_isr, "Ethernet_RX_Frame")

    @classmethod
    def get_rx_frame(
        cls, interface_id: InterfaceId, get_time: bool = False
    ) -> Union[Optional[bytes], Tuple[Optional[bytes], Optional[float]]]:
        frame = None
        rx_time = None
        log.info("Checking for: %s" % str(interface_id))
        if cls.frame_queues[interface_id]:
            log.info("Returning frame")
            frame = cls.frame_queues[interface_id].popleft()
            rx_time = cls.frame_times[interface_id].popleft()

        if get_time:
            return frame, rx_time
        else:
            return frame

    @classmethod
    def get_rx_frame_only(cls, interface_id: InterfaceId) -> Optional[bytes]:
        ret = cls.get_rx_frame(interface_id, False)
        assert ret is None or isinstance(ret, bytes)
        return ret

    @classmethod
    def get_rx_frame_and_time(
        cls, interface_id: InterfaceId
    ) -> Tuple[Optional[bytes], Optional[float]]:
        ret = cls.get_rx_frame(interface_id, True)
        assert isinstance(ret, tuple)
        assert len(ret) == 2
        assert ret[0] is None or isinstance(ret[0], bytes)
        assert ret[1] is None or isinstance(ret[1], float)
        return ret

    @classmethod
    def get_frame_info(cls, interface_id: InterfaceId) -> Tuple[int, int]:
        """
            return number of frames and length of first frame
        """
        queue = cls.frame_queues[interface_id]
        if queue:
            return len(queue), len(queue[0])
        return 0, 0

