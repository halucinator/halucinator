# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""
Peripheral model for tty device
"""
import logging
from collections import deque

from . import peripheral_server
from .interrupts import Interrupts

log = logging.getLogger(__name__)


class UTTYInterface:
    """
    Defines a single utty device. One instance created by UTTYModel per interface
    """

    def __init__(self, interface_id, enabled=True, irq_num=None):
        self.interface_id = interface_id
        self.rx_queue = deque()
        self.tx_queue = deque()
        self.irq_num = irq_num
        self.enabled = enabled
        self.irq_enabled = True

    def enable(self):
        """
        Enable this interface
        """
        self.enabled = True

    def disable(self):
        """
        Disable this interface
        """
        self.enabled = False

    def flush(self):
        """
        Flush the rx buffer
        """
        self.rx_queue.clear()

    def clear_irq(self):
        """
        Clear active interrupt
        """
        Interrupts.clear_active_bp(self.irq_num)

    def disable_irq(self):
        """
        Disable interrupts
        """
        self.irq_enabled = False

    def enable_irq_bp(self):
        """
        Enable a intterupts callable from bp_handler
        """
        Interrupts.clear_active_bp(self.irq_num)

    def _fire_interrupt_bp(self):
        """
        Triggers and interrupt by writting to the IRQ controllers MMIO registers
        use if need to trigger interrupt inside bp_handler
        """
        if self.rx_queue and self.irq_num:
            Interrupts.set_active_bp(self.irq_num)

    def _fire_interrupt_qmp(self):
        """
        Uses QMP interface to trigger and interrupt in the target
        """
        if self.rx_queue and self.irq_num:
            log.debug("Sending Interupt for %s: %#x", self.interface_id, self.irq_num)
            Interrupts.set_active_qmp(self.irq_num)

    def buffer_rx_chars_qmp(self, chars):
        """
        Buffers the received characters into a buffer then trigger interrupt over qmp
        Should not be called from bp_handler
        """
        if self.enabled:
            log.info("Adding chars to: %s", self.interface_id)
            for char in chars:
                self.rx_queue.append(char)
            # self.rxchar_times.append(time.time())

            self._fire_interrupt_qmp()
        else:
            log.info("%s Not enabled: ", self.interface_id)
            return

    def get_rx_char(self, get_time=False):  # pylint: disable=unused-argument
        """
        Reads a byte from the rx buffer
        """
        char = None
        if self.rx_queue:
            char = self.rx_queue.popleft()
            if not self.rx_queue:
                self.clear_irq()
            # rx_time = self.frame_times.popleft()
        else:
            return 0x00
        return char

    def get_rx_buff_size(self):
        """
        Gets number of bytes in rx buffer
        """
        if self.rx_queue:
            return len(self.rx_queue)
        return 0

    def buffer_tx_char_qmp(self, char):
        """
        Transmits a character and trigger an interrupt using qmp interface
        Should not be called from inside a bp_handler
        """
        if self.enabled:
            self.tx_queue.append(char)
            # self.txchar_times.append(time.time())
            log.info("Adding char to: %s", self.interface_id)
            self._fire_interrupt_qmp()
        else:
            return

    def get_tx_char(self, get_time=False):  # pylint: disable=unused-argument
        """
        Reads a character from the tx buffer
        """
        char = None

        if self.tx_queue:
            char = self.tx_queue.popleft()
        return char

    def get_tx_buff_size(self):
        """
        Gets the tx buffer size
        """
        if self.tx_queue:
            return len(self.tx_queue)
        return 0

    # def get_frame_info(self):
    #     '''
    #         Returns the number of frames in the Queue and number of
    #         len of first frame
    #     '''
    #     if self.rx_queue:
    #         return len(self.rx_queue), len(self.rx_queue[0])
    #     return 0, 0


# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class UTTYModel:
    """
    The peripheral server peripheral model for tty device
    """

    interfaces = {}
    unattached_interfaces = {}

    @classmethod
    def add_interface(cls, interface_id, enabled=True, irq_num=None):
        """
        Adds an interface to the model.  Enables support more than one tty device
        """
        interface = UTTYInterface(interface_id, enabled=enabled, irq_num=irq_num)
        cls.unattached_interfaces[interface_id] = interface
        # cls.interfaces[interface_id] = interface

    @classmethod
    def attach_interface(cls, interface_id):
        """
        Attaches previouly created interface so that it is active. Prevents race condition
        where interrupts could be triggered prior to the interface being made active.
        """
        interface = cls.unattached_interfaces.pop(interface_id, None)
        if interface is not None:
            cls.interfaces[interface_id] = interface
            return True
        return False

    # @classmethod
    # def enable_rx_isr_bp(cls, interface_id):
    #     cls.interfaces[interface_id].enable_irq_bp()

    # @classmethod
    # def disable_rx_isr_bp(cls, interface_id):
    #     cls.interfaces[interface_id].disable_irq()

    @classmethod
    def enable(cls, interface_id):
        """
        Enables the interface
        """
        cls.interfaces[interface_id].enable()

    @classmethod
    def flush(cls, interface_id):
        """
        Flushes data from the buffer
        """
        cls.interfaces[interface_id].flush()

    @classmethod
    def disable(cls, interface_id):
        """
        Disables this interface
        """
        cls.interfaces[interface_id].disable()

    @classmethod
    @peripheral_server.tx_msg
    def tx_buf(cls, interface_id, buf):
        """
        Creates the message that Peripheral.tx_msga will send on this
        event
        """
        msg = {"interface_id": interface_id, "chars": buf}
        return msg

    @classmethod
    @peripheral_server.reg_rx_handler
    def rx_char_or_buf(cls, msg):
        """
        Processes reception of this type of message from
        PeripheralServer.rx_msg
        """
        interface_id = msg["interface_id"]
        try:
            interface = cls.interfaces[interface_id]
            if isinstance(msg["char"], int):
                log.info("Adding char to: %s", interface_id)
                char = msg["char"]
                interface.buffer_rx_chars_qmp([char])
            else:
                char_buff = msg["char"]
                log.info("Adding buffer to: %s", interface_id)
                interface.buffer_rx_chars_qmp(char_buff)
        except KeyError:
            log.info("No interface attached for %s", interface_id)

    @classmethod
    def get_rx_char(cls, interface_id, get_time=False):
        """
        Reads a character from the rx buffer.
        """
        log.info("Getting RX char from: %s", interface_id)
        interface = cls.interfaces[interface_id]
        char = interface.get_rx_char(get_time)
        try:
            char = ord(char)
        except TypeError:
            pass
        return char

    @classmethod
    def get_rx_buff_size(cls, interface_id):
        """
        Gets the number of bytes in the received buffer.
        Called from bp_handlers
        """
        log.info("Getting RX buff size from: %s", interface_id)
        interface = cls.interfaces[interface_id]

        return interface.get_rx_buff_size()

    # @classmethod
    # def get_frame_info(cls, interface_id):
    #     '''
    #         return number of frames and length of first frame
    #     '''
    #     interface = cls.interfaces[interface_id]
    #     return interface.get_frame_info()
