# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

from typing import TYPE_CHECKING

from ..bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
import logging
import time

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget
log = logging.getLogger(__name__)


class Contiki(BPHandler):

    def __init__(self, model: None = None):
        BPHandler.__init__(self)
        self.model = model
        self.start_time = time.time()
        self.ticks_per_second = 128

    def register_handler(self, qemu: HALQemuTarget, addr: int, func_name: str, ticks_per_second: int = None) -> HandlerFunction:
        if ticks_per_second is not None:
            self.ticks_per_second = ticks_per_second
        return BPHandler.register_handler(self, qemu, addr, func_name)

    @bp_handler(['clock_time'])
    def clock_time(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        ticks = time.time() - self.start_time
        ticks = int(ticks * self.ticks_per_second)
        log.debug("#Ticks: %i" % ticks)
        return True, ticks

    @bp_handler(['clock_seconds'])
    def clock_seconds(self, qemu: HALQemuTarget, bp_addr: int) -> HandlerReturn:
        secs = int(time.time() - self.start_time)
        log.debug("#Seconds: %i" % secs)
        return True, secs
