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
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget

# sys.path.insert(0,path.dirname(path.dirname(path.abspath(__file__))))


class Counter(BPHandler):
    '''
        Returns an increasing value for each addresss accessed

        Halucinator configuration usage:
        - class: halucinator.bp_handlers.Counter
          function: <func_name> (Can be anything)
          addr: <addr>
          registration_args:{increment:1} (Optional)
    '''

    def __init__(self) -> None:
        self.increment: Dict[int, int] = {}
        self.counts: Dict[int, int] = {}

    def register_handler(self, qemu: HALQemuTarget, addr: int, func_name: str, increment: int = 1) -> HandlerFunction:
        '''

        '''
        self.increment[addr] = increment
        self.counts[addr] = 0

        return cast(HandlerFunction, Counter.get_value)

    @bp_handler
    def get_value(self, qemu: HALQemuTarget, addr: int) -> HandlerReturn:
        '''
            Gets the counter value
        '''
        self.counts[addr] += self.increment[addr]
        return True, self.counts[addr]
