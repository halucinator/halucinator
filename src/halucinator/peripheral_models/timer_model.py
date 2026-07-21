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
            # Drop the entry so a later start_timer(name) can spawn a FRESH
            # thread. Without this the stopped (dead) entry lingers and the
            # `if name not in active_timers` guard in start_timer turns every
            # re-arm into a no-op — e.g. GRBL's step timer never restarts for
            # a second move. The signalled thread exits on its own stop_event.
            del cls.active_timers[name]

    @classmethod
    def clear_timer(cls, irq_name: str) -> None:
        # cls.stop_timer(name)
        Interrupts.clear_active(irq_name)

    @classmethod
    def shutdown(cls) -> None:
        for key, (stop_event, t) in list(cls.active_timers.items()):
            stop_event.set()

    # -- snapshot (see doc/snapshot_restore.md) ------------------------
    # active_timers holds live Event/Thread pairs — process-local plumbing
    # the generic deep-copy can't (and shouldn't) capture. The machine
    # state of a timer is its parameters; the thread is reconstructable.

    @classmethod
    def save_state(cls) -> Dict[str, dict]:
        timers = {}
        for name, (stop_event, t) in cls.active_timers.items():
            timers[name] = {
                "irq_num": t.irq_num,
                "rate": t.rate,
                "delay": t.delay,
                "stopped": stop_event.is_set() or not t.is_alive(),
            }
        return {"timers": timers}

    @classmethod
    def restore_state(cls, state: Dict[str, dict]) -> bool:
        """Stop every live timer, then relaunch the saved set. Tick phase is
        not preserved — a restored periodic timer starts a fresh period,
        which is indistinguishable to the guest.

        Old timer threads are JOINED (not just signalled) before relaunching,
        so a stale tick from a pre-restore timer can't fire an IRQ after
        restore has reported success and the guest resumes."""
        old = list(cls.active_timers.values())
        for stop_event, _t in old:
            stop_event.set()
        # Event.set() wakes a thread parked in stopped.wait(rate) immediately;
        # join with a bounded timeout so a wedged tick callback can't hang
        # restore forever (the thread is a daemon and will not outlive us).
        for _stop_event, t in old:
            if t.is_alive():
                t.join(timeout=2.0)
        cls.active_timers.clear()
        for name, cfg in state.get("timers", {}).items():
            if cfg.get("stopped"):
                continue
            cls.start_timer(name, cfg["irq_num"], cfg["rate"],
                            cfg.get("delay", 0))
        return True


class TimerIRQ(Thread):
    def __init__(self, event: Event, irq_name: str, irq_num: int, rate: float, delay: int = 0) -> None:
        Thread.__init__(self)
        # Daemon so a running tick timer never blocks interpreter shutdown:
        # the run loop only exits when stop_event is set, and on an abrupt
        # emulation end (fault / emu_stop) nothing sets it, which otherwise
        # hangs Py_FinalizeEx waiting to join this non-daemon thread.
        self.daemon = True
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
        # Diagnostic file-log bypasses logger-level filtering; opt in via
        # HAL_TIMER_DBG=path env var.
        import os as __os
        _dbg = __os.environ.get("HAL_TIMER_DBG")
        _dbg_f = open(_dbg, "a") if _dbg else None
        if _dbg_f is not None:
            _dbg_f.write("timer-thread-start irq=%d rate=%g\n"
                         % (self.irq_num, self.rate))
            _dbg_f.flush()
        while not self.stopped.wait(self.rate):
            log.info("Sending IRQ: %s" % hex(self.irq_num))
            if _dbg_f is not None:
                _dbg_f.write("tick\n")
                _dbg_f.flush()
            Interrupts.Active_Interrupts[self.name] = True
            Interrupts.set_active_qmp(self.irq_num)
            # call a function
