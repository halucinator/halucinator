# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

import logging
import time
from threading import Event, Thread
from typing import Dict, Tuple

from halucinator.peripheral_models import peripheral_server
from halucinator.peripheral_models.interrupts import Interrupts

log = logging.getLogger(__name__)

# Register the pub/sub calls and methods that need mapped
@peripheral_server.peripheral_model
class TimerModel(object):

    active_timers: Dict[str, Tuple[Event, "TimerIRQ"]] = {}

    @classmethod
    def start_timer(cls, name: str, isr_num: int, rate: float, delay: int = 0) -> None:
        log.debug("Starting timer: %s" % name)
        if name not in cls.active_timers:
            stop_event = Event()
            t = TimerIRQ(stop_event, name, isr_num, rate, delay)
            cls.active_timers[name] = (stop_event, t)
            t.start()

    @classmethod
    def stop_timer(cls, name: str) -> None:
        if name in cls.active_timers:
            (stop_event, t) = cls.active_timers[name]
            stop_event.set()

    @classmethod
    def clear_timer(cls, irq_name: str) -> None:
        # cls.stop_timer(name)
        Interrupts.clear_active(irq_name)

    @classmethod
    def shutdown(cls) -> None:
        for key, (stop_event, t) in list(cls.active_timers.items()):
            stop_event.set()


class TimerIRQ(Thread):
    def __init__(self, event: Event, irq_name: str, irq_num: int, rate: float, delay: int = 0) -> None:
        Thread.__init__(self)
        self.stopped: Event = event
        self.name: str = irq_name
        self.irq_num: int = irq_num
        self.rate: float = rate
        self.delay: int = delay

    def run(self) -> None:
        if self.delay:
            #delay for self.delay seconds before triggering
            time.sleep(self.delay)
            self.delay = 0
        Interrupts.enabled[self.irq_num] = True
        while not self.stopped.wait(self.rate):
            log.info("Sending IRQ: %s" % hex(self.irq_num))
            Interrupts.Active_Interrupts[self.name] = True
            Interrupts.set_active_qmp(self.irq_num)
            # call a function
