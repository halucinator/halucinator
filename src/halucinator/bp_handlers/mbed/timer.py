# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from time import sleep
from typing import TYPE_CHECKING, Any

from ..bp_handler import BPHandler, HandlerReturn, bp_handler
import struct

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget
import logging
log = logging.getLogger(__name__)


class MbedTimer(BPHandler):

    def __init__(self, impl: Any = None):
        pass

    @bp_handler(['wait'])
    def wait(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        log.info("MBed Wait")
        param0 = qemu.regs.r0  # a floating point value
        value = struct.pack("<I", param0)
        stuff = struct.unpack("<f", value)[0]
        sleep(stuff)
        return False, 0  # , (param0,)

# TODO: Timer-based callbacks
