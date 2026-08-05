# Copyright 2026 Christopher Wright

"""x86 PC interrupt delivery for the in-process UnicornBackend.

On a PC, an external interrupt (the 8254 PIT clock tick routed through
the 8259 PIC, IRQ0 -> IDT vector 0x20) makes the CPU push the current
EFLAGS/CS/EIP onto the stack and vector to the IDT entry's handler. The
handler runs and returns with `iret`, which pops that frame.

Unicorn's in-process x86 model does not deliver hardware interrupts on
its own, and the i386 VxWorks RTU image never sets up a real 8259/8254 we
could drive — the BSP timer init is hardware-timing bound and is stubbed.
So this controller *synthesises* the interrupt entry directly:

  * It reproduces the VxWorks i86 interrupt stub
    (`intHandlerCreateI86` builds one per connected ISR):

        cli
        call intEnt          ; kernel interrupt bookkeeping
        push <isrArg>
        call <isr>           ; the connected C routine (usrClock)
        add  esp, 4
        jmp  intExit         ; reschedule + iretd back / into a task

    We assemble an equivalent stub once in guest RAM. Routing the tick
    through the firmware's *own* `intEnt`/`intExit` is what makes the
    scheduler actually preempt the idle spin: `intExit` (0x4362d0 in
    this image) ends by pushing `reschedule` and `iretd`-ing into it,
    so a newly-ready task is dispatched.

  * On delivery it builds the CPU interrupt frame the stub expects
    (EFLAGS, CS, EIP of the interrupted instruction) on the current
    stack and sets EIP to the stub. The firmware's `intExit` issues the
    final `iret`; UnicornBackend's flat-segment recovery handles that
    far transfer.

Delivery mutates EIP/ESP and must run on the dispatch thread (Unicorn is
not safe against register writes mid-`emu_start`). The timer fires from a
background thread, so `trigger()` only *queues* — UnicornBackend.cont()
calls `deliver()` between `emu_start` chunks (see `_apply_pending_irq`).

Config (machine.interrupt_controller in YAML)::

    interrupt_controller:
      type: x86_pic
      options:
        isr_addr:    0x410e50   # connected clock routine (usrClock)
        int_ent:     0x436240   # intEnt
        int_exit:    0x4362d0   # intExit
        stub_addr:   0x00007000 # scratch guest RAM to assemble the stub
        isr_arg:     0          # argument pushed to the ISR

`isr_addr` may be omitted and learned at run time from the
`sysClkConnect`/`vxbSysClkConnect` interception (which records the
connected routine via `register_clock_isr`). `int_ent`/`int_exit` may be
omitted to vector straight at the C ISR with a bare iret frame (no kernel
bookkeeping); routing through the stub is strongly preferred because the
scheduler reschedule lives in `intExit`.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, List, Optional

from . import IrqController

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)


class X86PicController(IrqController):
    """Synthesised PC interrupt delivery for UnicornBackend (x86/i386).

    A single instance is shared between the timer thread (calls
    `trigger`) and the dispatch thread (calls `deliver`). It is the
    rendezvous point for the connected clock-ISR address learned from
    the `sysClkConnect` interception.
    """

    name = "x86_pic"

    def __init__(
        self,
        isr_addr: Optional[int] = None,
        int_ent: Optional[int] = None,
        int_exit: Optional[int] = None,
        stub_addr: int = 0x7000,
        isr_arg: int = 0,
        vector: int = 0x20,
        options: Optional[dict] = None,
    ) -> None:
        self.isr_addr: Optional[int] = isr_addr
        self.int_ent: Optional[int] = int_ent
        self.int_exit: Optional[int] = int_exit
        self.stub_addr: int = stub_addr
        self.isr_arg: int = isr_arg
        self.vector: int = vector
        # Re-entrancy guard: don't nest a tick inside the clock ISR.
        self._in_isr: bool = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # ISR registration (called by the sysClkConnect bp_handler)
    # ------------------------------------------------------------------

    def register_clock_isr(self, addr: int) -> None:
        """Record the C routine the firmware connected as its clock ISR.

        Wins over a YAML-configured `isr_addr` only when the latter was
        left unset, so an explicit config value is authoritative.
        """
        if addr and self.isr_addr is None:
            log.info("x86_pic: learned clock ISR @ 0x%08x from "
                     "sysClkConnect", addr)
            self.isr_addr = addr

    # ------------------------------------------------------------------
    # IrqController interface — queue from the timer thread
    # ------------------------------------------------------------------

    def trigger(self, backend: "HalBackend", num: int) -> None:
        """Queue an interrupt for delivery on the dispatch thread.

        UnicornBackend.cont() drains `backend._pending_irqs` between
        emu_start chunks (the x86 path bounds each chunk by instruction
        count for exactly this reason) and calls `_apply_pending_irq`,
        which routes x86 back to this controller's `deliver()`.

        We ONLY append here. We must NOT call uc.emu_stop() from this
        (timer) thread: unicorn is not thread-safe, and emu_stop() from a
        second thread deadlocks against the dispatch thread (the timer
        thread blocks in emu_stop holding the GIL while the dispatch
        thread holds unicorn's internal lock inside emu_start waiting on
        the GIL). The chunked emu_start lets the dispatch thread notice
        the queued IRQ on its own."""
        pend: List[int] = getattr(backend, "_pending_irqs", None)
        if pend is None:
            # Backend doesn't support deferred delivery — deliver inline
            # (best-effort; only safe if not mid-run).
            self.deliver(backend)
            return
        pend.append(int(num))

    # ------------------------------------------------------------------
    # Delivery — runs on the dispatch thread (single-threaded CPU state)
    # ------------------------------------------------------------------

    def deliver(self, backend: "HalBackend") -> bool:
        """Deliver a queued tick. Thin shim over ``X86ExceptionDeliverer`` —
        the exception-entry logic now lives in the deliverer (mirroring how
        ``ArmVicController`` delegates to ``ArmExceptionDeliverer``). This
        entry point stays because the in-process dispatch loop calls it; it
        builds a plan from this controller's live fields (``isr_addr`` is
        learned at run time via ``register_clock_isr``) and delegates.

        Returns True if the entry was set up, False if suppressed (IRQs
        masked or no ISR known)."""
        from .delivery import (
            DeliveryModel, DeliveryPlan, X86ExceptionDeliverer)
        plan = DeliveryPlan(
            model=DeliveryModel.FRAME,
            isr_addr=self.isr_addr,
            extra={"int_ent": self.int_ent, "int_exit": self.int_exit,
                   "stub_addr": self.stub_addr, "isr_arg": self.isr_arg},
        )
        return X86ExceptionDeliverer().deliver(backend, 0, plan)

    def on_isr_return(self) -> None:
        """Clear the in-ISR guard. Called by the backend when the
        handler's iret unwinds back to non-ISR code."""
        with self._lock:
            self._in_isr = False
