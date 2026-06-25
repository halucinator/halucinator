# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import re
from binascii import hexlify
from os import path
import sys
from typing import TYPE_CHECKING, Dict, cast

from ..bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

# sys.path.insert(0,path.dirname(path.dirname(path.abspath(__file__))))


class Counter(BPHandler):
    '''
        Returns an increasing value in r0 on each access -- (previous + increment)
        & mask, starting from `start`. Models a free-running counter/timer whose
        backing store does not advance in the re-host (so elapsed-time / timeout
        loops never progress): each call advances it by `increment`. The `mask`
        wraps it to a register width (e.g. 0xffff for a 16-bit counter).

        Halucinator configuration usage:
        - class: halucinator.bp_handlers.Counter
          function: <func_name> (Can be anything)
          addr: <addr>
          registration_args: { increment: 1, mask: 0xffffffff, start: 0 }  (all optional)
    '''

    def __init__(self) -> None:
        self.increment: Dict[int, int] = {}
        self.counts: Dict[int, int] = {}
        self.mask: Dict[int, int] = {}

    def register_handler(self, qemu: "HalBackend", addr: int, func_name: str,
                         increment: int = 1, mask: int = 0xFFFFFFFF, start: int = 0) -> HandlerFunction:
        '''

        '''
        self.increment[addr] = int(increment)
        self.counts[addr] = int(start)
        self.mask[addr] = int(mask)

        return cast(HandlerFunction, Counter.get_value)

    @bp_handler
    def get_value(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        '''
            Gets the counter value
        '''
        mask = self.mask.get(addr, 0xFFFFFFFF)
        self.counts[addr] = (self.counts[addr] + self.increment[addr]) & mask
        return True, self.counts[addr]