"""ARM (A-profile, ARMv5/v6/v7-A) external-IRQ delivery for the
in-process UnicornBackend.

This follows the same threading discipline as the synthesised-IRQ
approach used for the x86 PC path: a periodic timer thread only *queues* the
interrupt (``trigger``), and the dispatch thread performs the actual
CPU-state mutation (``deliver``) between ``emu_start`` chunks. Unicorn is
not safe against register writes mid-``emu_start`` and a cross-thread
``uc.emu_stop()`` deadlocks it, so all PC/CPSR/banked-register changes
happen on the dispatch thread.

Why a "VIC" controller separate from the GIC one
-------------------------------------------------
HALucinator already ships ``backends/irq/gic.py`` for GICv2/v3 SoCs (it
does a real ``GICD_ISPENDR`` MMIO write and relies on the firmware's GIC
to take the exception). Many older ARM SoCs — including the ARM926EJ-S in
some ARMv5 VxWorks PLCs — do **not** have a GIC. They
use a vendor on-chip interrupt controller (a PrimeCell VIC-style or a
fully custom block, on this SoC the system controller window at
``0xfffff000``). Unicorn models none of these, so for those targets the
controller must *synthesise* the architectural IRQ exception entry
directly, exactly like the synthesised-IRQ approach used for the x86 PC
path builds the PC interrupt frame.

Architectural IRQ entry reproduced here (ARM ARM, A2.6.8 / B1.8.3)::

    R14_irq  (LR_irq)  = address of interrupted instruction + 4
    SPSR_irq           = CPSR (the pre-exception state)
    CPSR.M[4:0]        = 0b10010                (IRQ mode, 0x12)
    CPSR.I             = 1                       (mask further IRQs)
    CPSR.T             = 0                       (execute the vector in ARM
                                                  state)
    CPSR.E             = SCTLR.EE               (left unchanged here)
    PC                 = vector_base + 0x18      (the IRQ vector)

``vector_base`` is ``0x00000000`` when SCTLR.V==0 (low/normal vectors)
and ``0xffff0000`` when SCTLR.V==1 (high vectors). The target PLC reset stub
sets SCTLR=0xc0000278 (V bit clear), so the default here is the low
vector base. The word at ``vector_base + 0x18`` is the firmware's IRQ
vector instruction — on VxWorks/ARM it is typically
``ldr pc, [pc, #offset]`` that loads the address of the kernel's
``intEnt``/``_irq_entry`` stub from the table just past the vectors.

When the firmware has not yet installed its exception vectors (e.g. the
rehost has not reached ``excVecInit``), the YAML can instead point the
controller straight at the connected ISR with ``isr_addr`` (and an
optional ``irq_simple_entry`` trampoline that ends in ``mov pc, lr`` /
``bx lr``); delivery then sets LR to the interrupted PC and jumps at that
routine directly, which the ISR returns from with a plain
register-restoring return. Routing through the real vector at 0x18 is
preferred because the kernel's ``intExit``/reschedule lives behind it and
is what actually preempts a task.

Config (machine.interrupt_controller in YAML)::

    interrupt_controller:
      type: arm_vic            # or "arm" / "vic"
      options:
        vector_base: 0x0       # SCTLR.V==0 -> 0x0 ; V==1 -> 0xffff0000
        isr_addr:    0x20xxxxxx # connected clock ISR (optional; learned
                                #   from sysClkConnect if omitted)
        irq_simple_entry: 0x.. # optional AAPCS trampoline (LR=return PC)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, List, Optional

from . import IrqController
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()

# ARMv7-A CPSR mode + flag bits.
_ARM_MODE_IRQ = 0x12
_ARM_MODE_MASK = 0x1F
_ARM_CPSR_I = 0x80   # IRQ disable (mask) bit
_ARM_CPSR_T = 0x20   # Thumb-state bit

# Architectural IRQ vector offset within the exception vector table.
_IRQ_VECTOR_OFFSET = 0x18


class ArmVicController(IrqController):
    """Synthesised A-profile-ARM IRQ delivery for UnicornBackend.

    A single instance is shared between the timer thread (``trigger``)
    and the dispatch thread (``deliver``); it is also the rendezvous
    point for the clock-ISR address learned at run time from the
    ``sysClkConnect`` interception (``register_clock_isr``).
    """

    name = "arm_vic"

    def __init__(
        self,
        vector_base: int = 0x0,
        isr_addr: Optional[int] = None,
        irq_simple_entry: Optional[int] = None,
        options: Optional[dict] = None,
    ) -> None:
        self.vector_base: int = vector_base
        self.isr_addr: Optional[int] = isr_addr
        self.irq_simple_entry: Optional[int] = irq_simple_entry
        self.options: dict = options or {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # ISR registration (called by the sysClkConnect bp_handler)
    # ------------------------------------------------------------------

    def register_clock_isr(self, addr: int) -> None:
        """Record the routine the firmware connected as its clock ISR.

        Only used by the ``isr_addr``/``irq_simple_entry`` direct-vector
        fallback (when the firmware vectors at 0x18 are not yet
        installed). A YAML-configured ``isr_addr`` is authoritative — we
        only fill it in when it was left unset."""
        if addr and self.isr_addr is None:
            log.info("arm_vic: learned clock ISR @ 0x%08x from sysClkConnect",
                     addr)
            self.isr_addr = addr

    # ------------------------------------------------------------------
    # IrqController interface — queue from the timer thread
    # ------------------------------------------------------------------

    def trigger(self, backend: "HalBackend", num: int) -> None:
        """Queue an interrupt for delivery on the dispatch thread.

        Mirrors the synthesised-IRQ approach used for the x86 PC path: we
        ONLY append to ``backend._pending_irqs``.
        We must NOT touch unicorn from this (timer) thread — unicorn is
        not thread-safe and a cross-thread ``emu_stop()`` deadlocks the
        dispatch thread. ``UnicornBackend.cont()`` runs ARM in bounded
        chunks (the ``irq_chunk`` gate now includes "arm") and drains
        the queue via ``_apply_pending_irq`` -> this controller's
        ``deliver`` between chunks."""
        pend: List[int] = getattr(backend, "_pending_irqs", None)
        if pend is None:
            # Backend has no deferred-delivery queue: best effort inline.
            self.deliver(backend, num)
            return
        pend.append(int(num))
        import os as __os
        _dbg = __os.environ.get("HAL_TIMER_DBG")
        if _dbg:
            try:
                with open(_dbg, "a") as f:
                    f.write("trigger queued irq=%d pend_depth=%d\n"
                            % (num, len(pend)))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Delivery — runs on the dispatch thread (single-threaded CPU state)
    # ------------------------------------------------------------------

    def deliver(self, backend: "HalBackend", num: int = 0) -> bool:
        """Synthesise the ARM IRQ exception entry. Must run single-threaded.

        Thin legacy wrapper: the exception-entry logic now lives in the
        shared ``ArmExceptionDeliverer`` (backends/irq/delivery.py). This
        controller's fields (``vector_base``/``isr_addr``/``irq_simple_entry``)
        map onto a ``DeliveryPlan`` and delegate, so there is exactly one
        copy of the ARM IRQ-entry sequence. Kept for configs that wire an
        ``ArmVicController`` directly (e.g. via ``set_irq_controller`` with
        no separate delivery plan) and for the ``register_clock_isr``
        rendezvous below.

        Returns True if the entry was set up, False if suppressed (IRQs
        masked) — the diagnostic logging is retained here."""
        from .delivery import (ArmExceptionDeliverer, DeliveryModel,
                               DeliveryPlan)
        import os as __os
        _dbg = __os.environ.get("HAL_TIMER_DBG")
        if _dbg:
            try:
                pc_now = backend.read_register("pc")
                cpsr_now = backend.read_register("cpsr")
                with open(_dbg, "a") as f:
                    f.write("deliver enter irq=%d pc=0x%08x cpsr=0x%x "
                            "isr_addr=%s\n"
                            % (num, pc_now, cpsr_now,
                               hex(self.isr_addr) if self.isr_addr else "None"))
            except Exception:
                pass
        with self._lock:
            model = (DeliveryModel.TRAMPOLINE
                     if self.irq_simple_entry is not None
                     else DeliveryModel.FRAME)
            plan = DeliveryPlan(
                model=model,
                vector_base=self.vector_base,
                isr_addr=self.isr_addr,
                trampoline=self.irq_simple_entry,
            )
            delivered = ArmExceptionDeliverer().deliver(backend, num, plan)
            if not delivered:
                log.debug("arm_vic: CPSR.I=1 (IRQs masked) — tick deferred")
            return delivered

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _vector_installed(self, backend: "HalBackend") -> bool:
        """Heuristic: is a real IRQ vector present at vector_base+0x18?

        A non-zero word there means the firmware installed its exception
        vectors (typically `ldr pc,[pc,#off]`); a zero word means we are
        running before excVecInit and must vector at the ISR directly."""
        try:
            word = backend.read_memory(self.vector_base + _IRQ_VECTOR_OFFSET,
                                       4, 1)
        except Exception:  # noqa: BLE001
            return False
        # read_memory returns an int for a single word.
        return int(word) != 0
