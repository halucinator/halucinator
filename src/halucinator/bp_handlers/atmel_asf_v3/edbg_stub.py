# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from typing import TYPE_CHECKING

from ..intercepts import tx_map, rx_map
from ..bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
from collections import defaultdict, deque

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend
import struct
import binascii
import os
import logging
import time
log = logging.getLogger(__name__)


# This is just a stub to enable getting edbg_eui for use as MAC
# on 6LoWPAN example apps


class EDBG_Stub(BPHandler):

    def __init__(self, model: None = None):
        BPHandler.__init__(self)
        self.model = model
        self.eui64 = ''

    def register_handler(self, qemu: "HalBackend", addr: int, func_name: str, eui64: str = None) -> HandlerFunction:
        if eui64 is not None:
            self.eui64 = eui64
        return BPHandler.register_handler(self, qemu, addr, func_name)

    @bp_handler(['i2c_master_init', 'i2c_master_enable'])
    def return_void(self, qemu: "HalBackend", bp_addr: int) -> HandlerReturn:
        return True, None

    @bp_handler(['i2c_master_write_packet_wait_no_stop'])
    def return_ok(self, qemu: "HalBackend", bp_addr: int) -> HandlerReturn:
        return True, 0

    @bp_handler(['i2c_master_read_packet_wait'])
    def get_edbg_eui64(self, qemu: "HalBackend", bp_addr: int) -> HandlerReturn:
        packet = qemu.regs.r1
        packet_struct = qemu.read_memory(packet+2, 1, 6, raw=True)
        (length, data_ptr) = struct.unpack("<HI", packet_struct)
        if length > len(self.eui64):
            eui64 = self.eui64 + "\55"*(length - len(self.eui64))
            qemu.write_memory(data_ptr, 1, eui64, len(eui64), raw=True)
        else:
            qemu.write_memory(data_ptr, 1, self.eui64, length, raw=True)
        return True, 0