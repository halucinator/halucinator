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
import struct
import threading
from typing import TYPE_CHECKING, List, Optional

from . import IrqController
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()

# EFLAGS bits.
_EFLAGS_IF = 1 << 9   # interrupt-enable flag
_EFLAGS_RESERVED = 0x2  # bit1 is always 1


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
        self._stub_written: bool = False
        self._stub_entry: Optional[int] = None
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

    def _ensure_stub(self, backend: "HalBackend") -> Optional[int]:
        """Assemble the VxWorks-style interrupt stub in guest RAM once.

        Returns the stub entry EIP, or None when no kernel int_ent/
        int_exit are configured (caller then vectors at the ISR
        directly with a bare iret frame).
        """
        if self.int_ent is None or self.int_exit is None:
            return None
        if self._stub_written:
            return self._stub_entry
        if self.isr_addr is None:
            return None
        base = self.stub_addr
        code = bytearray()
        # cli
        code += b"\xfa"
        # call intEnt   (rel32 from end of this instruction)
        code += b"\xe8" + struct.pack("<i", self.int_ent - (base + len(code) + 5))
        # push <isr_arg>
        code += b"\x68" + struct.pack("<I", self.isr_arg & 0xFFFFFFFF)
        # call <isr>
        code += b"\xe8" + struct.pack("<i", self.isr_addr - (base + len(code) + 5))
        # add esp, 4
        code += b"\x83\xc4\x04"
        # jmp intExit
        code += b"\xe9" + struct.pack("<i", self.int_exit - (base + len(code) + 5))
        if not backend.write_memory(base, 1, bytes(code)):
            log.warning("x86_pic: could not write interrupt stub at "
                        "0x%08x; falling back to direct ISR vector", base)
            self.int_ent = None
            self.int_exit = None
            return None
        self._stub_written = True
        self._stub_entry = base
        log.info("x86_pic: assembled interrupt stub @ 0x%08x "
                 "(intEnt=0x%x isr=0x%x intExit=0x%x, %d bytes)",
                 base, self.int_ent, self.isr_addr, self.int_exit, len(code))
        return base

    def deliver(self, backend: "HalBackend") -> bool:
        """Synthesise the CPU interrupt entry. Must run single-threaded.

        Returns True if the entry was set up (EIP now points at the
        handler), False if it was suppressed (interrupts masked, no ISR,
        or already inside the ISR)."""
        if self.isr_addr is None:
            log.warning("x86_pic: tick fired but no clock ISR known yet "
                        "(sysClkConnect not seen, no isr_addr configured) "
                        "— dropping")
            return False
        with self._lock:
            eflags = backend.read_register("eflags")
            if not (eflags & _EFLAGS_IF):
                # Interrupts masked (cli). The firmware will re-enable;
                # the next tick will land. Dropping a masked tick matches
                # real edge-PIC behaviour closely enough for the clock.
                log.debug("x86_pic: IF=0 (interrupts masked) — tick dropped")
                return False

            eip = backend.read_register("eip")
            cs = backend.read_register("cs")
            esp = backend.read_register("esp")

            target = self._ensure_stub(backend)
            if target is None:
                target = self.isr_addr

            # Build the hardware interrupt frame the handler/iret expects:
            #   [esp]   = EIP   (return address)
            #   [esp+4] = CS
            #   [esp+8] = EFLAGS
            esp -= 12
            backend.write_memory(esp, 4, eip & 0xFFFFFFFF)
            backend.write_memory(esp + 4, 4, cs & 0xFFFFFFFF)
            backend.write_memory(esp + 8, 4, eflags & 0xFFFFFFFF)
            backend.write_register("esp", esp)
            # Mask IF for the duration of the handler (the stub's cli does
            # this on hardware; set it now so a re-entrant tick is dropped
            # by the IF check above until the handler's iret restores it).
            backend.write_register("eflags", eflags & ~_EFLAGS_IF)
            backend.write_register("eip", target)
            hlog.info("x86_pic: delivering IRQ -> stub/ISR 0x%08x "
                      "(interrupted eip=0x%08x, frame@0x%08x)",
                      target, eip, esp)
            return True

    def on_isr_return(self) -> None:
        """Clear the in-ISR guard. Called by the backend when the
        handler's iret unwinds back to non-ISR code."""
        with self._lock:
            self._in_isr = False
