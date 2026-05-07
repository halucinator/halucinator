from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Tuple

from halucinator.bp_handlers import BPHandler, bp_handler
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log: logging.Logger = logging.getLogger(__name__)
hal_log = hal_log.getHalLogger()

class NewLibSysCalls(BPHandler):
    '''
        Break point handlers for NewLibSysCalls
    '''

    @bp_handler(['_write'])
    def _write(self, qemu: "HalBackend", addr: int) -> Tuple[bool, int]:
        '''
            Just print data to the screen and return
        '''
        addr = qemu.get_arg(1)
        l = qemu.get_arg(2)
        data = qemu.read_memory(addr, 1, l, raw=True)
        print(data.decode('ascii'),end="")
        return True, l
