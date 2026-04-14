# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from typing import TYPE_CHECKING, Type

from ...peripheral_models.uart import UARTPublisher
from ..intercepts import tx_map, rx_map
from ..bp_handler import BPHandler, HandlerReturn, bp_handler
import struct

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget
import logging
import binascii

log = logging.getLogger(__name__)


class USART(BPHandler):

    def __init__(self, impl: Type[UARTPublisher] = UARTPublisher):
        self.model = impl

    @bp_handler(['usart_init', 'usart_enable'])
    def return_ok(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        return True, 0

    @bp_handler(['usart_write_buffer_wait'])
    def write_buffer(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        # enum status_code usart_write_buffer_wait(
                #                                       struct usart_module *const module,
                #                                       const uint8_t *tx_data,
                #                                       uint16_t length);
        usart_ptr = qemu.regs.r0
        hw_addr = qemu.read_memory(usart_ptr, 4, 1)
        buf_addr = qemu.regs.r1
        buf_len = qemu.regs.r2
        data = qemu.read_memory(buf_addr, 1, buf_len, raw=True)
        log.info("Data %s" % data)
        self.model.write(hw_addr, data)
        return True, 0

    @bp_handler(['usart_write_wait'])
    def write_single(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        # enum status_code usart_write_buffer_wait(
                #                                       struct usart_module *const module,
                #                                       const uint16_t *tx_data);
        usart_ptr = qemu.regs.r0
        hw_addr = qemu.read_memory(usart_ptr, 4, 1)
        data = qemu.regs.r1
        log.debug("Tx_data: %s" % chr(data))
        self.model.write(hw_addr, chr(data))
        return True, 0

    @bp_handler(['usart_read_wait'])
    def read_single(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        usart_ptr = qemu.regs.r0
        hw_addr = qemu.read_memory(usart_ptr, 4, 1)
        ret = self.model.read(hw_addr, 1, block=True)[0]
        log.debug("Got Char: %s" % ret)
        qemu.write_memory(qemu.regs.r1, 2, ord(ret), 1)
        return True, 0
