# Copyright 2026 Christopher Wright
"""Shared in-process IRQ delivery machinery.

Backends whose CPU model does not take hardware exceptions (UnicornBackend,
GhidraBackend) must synthesise the interrupt entry on the dispatch thread.
This mixin owns the parts that are identical across those backends:

- the cross-thread pending-IRQ queue (``_pending_irqs``),
- the deterministic-tick policy (``HAL_DET_TICK``) and its double-tick guard,
- the ``_apply_pending_irq`` arch dispatcher,
- the SHADOW delivery path (endianness-correct, via ``ShadowExceptionDeliverer``),
- ``_resolve_delivery_plan`` (new ``irq_delivery`` config vs legacy controller),
- ``in_process_irq_active`` (the predicate main's dispatch loop uses to
  re-enter ``cont()`` after an async IRQ lands the CPU mid-ISR).

Backends provide a few primitives: ``arch``, ``_request_break`` (thread-safe
stop of the running emulator), and ``_apply_cortex_m_fallback`` (the
Cortex-M frame push, which uses each backend's native register/memory API).
A backend whose register model differs (Ghidra banks LR/SPSR under Sleigh
names) overrides ``_apply_pending_irq_armv7a`` / ``_apply_pending_irq_arm64``.
"""
from __future__ import annotations

import logging
import os
import struct
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


class InProcessIrqMixin:
    # Backends that deliver via a firmware-side shadow write (Ghidra) set
    # this True to prefer the shadow path whenever the controller carries
    # shadow addresses, even for arm/arm64 (it sidesteps banked-register
    # quirks). Unicorn leaves it False and uses the per-arch entries.
    _prefer_shadow_irq: bool = False

    # -- Cortex-M EXC_RETURN -----------------------------------------------
    # -- Cortex-M EXC_RETURN -----------------------------------------------
    # A PC in the EXC_RETURN range is an ISR doing `bx lr`. On a part with an
    # FPU the value also carries bit 4 = "no floating-point context stacked",
    # so an FP-context return is 0xFFFFFFE1/E9/ED.
    _EXC_RETURN_THREAD_MSP = 0xFFFFFFF9
    # Bits[31:7], not bits[31:5]. ARMv8-M puts two more flags below the v7-M
    # ones -- bit 6 = S (secure), bit 5 = DCRS -- so a plain non-secure thread
    # return is 0xFFFFFFBC, which a 0xFFFFFFE0 window misses. Nothing complains
    # when that happens: the value is not taken for an exception return, the
    # ISR's `bx lr` branches to 0xFFFFFFBC as if it were an address, and the
    # core faults there for the rest of the run while the host side carries on
    # reporting a healthy boot. An nRF9160 rehost produced 1.8 GB of
    # `CPU exception 3 at pc=0xffffffbc` in four minutes this way.
    #
    # Widening only ever adds values, and the test sweeps the old window to
    # prove it. 0xFFFFFF80..0xFFFFFFFF is reserved for EXC_RETURN, so there is
    # nothing else that could legitimately land in the range we gained.
    _EXC_RETURN_MASK = 0xFFFFFF80
    _EXC_RETURN_MAGIC = 0xFFFFFF80

    def _decode_exc_return_frame(self, pc: int):
        """If *pc* is a Cortex-M EXC_RETURN magic value, read and unpack the
        8-word exception frame pushed at SP. Returns ``(sp, frame)`` where
        ``frame`` is (r0,r1,r2,r3,r12,lr,pc,cpsr), or ``None`` if this isn't
        an exc-return. The register write-back and any emulator restart are
        backend-specific (register banking / FAULT-state differ), so callers
        apply those themselves."""
        if self.arch != "cortex-m3":
            return None
        if (pc & self._EXC_RETURN_MASK) != self._EXC_RETURN_MAGIC:
            return None
        sp = self.read_register("sp")
        try:
            frame = struct.unpack(
                "<8I", bytes(self.read_memory(sp, 1, 32, raw=True)))
        except Exception:  # noqa: BLE001
            return None
        return sp, frame

    # -- init --------------------------------------------------------------
    def _init_in_process_irq(self) -> None:
        """Initialise the pending-IRQ queue and deterministic-tick config.
        Call once from the backend ``__init__``."""
        # Pending IRQ injected from another thread (peripheral_server zmq
        # handler / TimerModel). The run loop drains the queue before
        # re-entering the CPU so the synthetic exception frame is set up
        # single-threaded.
        self._pending_irqs: List[int] = []
        # HAL_DET_TICK="<irq>:<chunks>" drives the system-clock IRQ from
        # instruction count in the run loop instead of the wall-clock timer
        # thread, for reproducible scheduling.
        self._det_irq: Optional[int] = None
        self._det_period: int = 0
        self._det_chunks: int = 0
        _det = os.environ.get("HAL_DET_TICK")
        if _det:
            try:
                _di, _dp = _det.split(":", 1)
                self._det_irq = int(_di, 0)
                self._det_period = max(1, int(_dp, 0))
            except Exception:  # noqa: BLE001
                self._det_irq = None

    # -- primitives the backend must provide -------------------------------
    def _request_break(self) -> None:
        """Ask the running emulator to stop (thread-safe). Backend-specific
        (Unicorn: uc.emu_stop(); Ghidra: emulator.setHalt(True))."""
        raise NotImplementedError

    def _apply_cortex_m_fallback(self, irq_num: int) -> None:
        """Push the Cortex-M 8-word exception frame and vector to
        vector[16+N]. Backend-specific (native register/memory API)."""
        raise NotImplementedError

    # -- deterministic tick ------------------------------------------------
    def _det_suppress(self, irq_num: int) -> bool:
        """True when this IRQ is the deterministic-tick IRQ (already driven
        from instruction count in the run loop) and so a wall-clock-thread
        injection of it should be dropped to avoid double-ticking."""
        return self._det_irq is not None and int(irq_num) == self._det_irq

    # -- delivery-plan resolution -----------------------------------------
    def _resolve_delivery_plan(self, build_legacy: Callable):
        """Return the attached DeliveryPlan (new ``irq_delivery`` config) or,
        when none was set, a plan built from the legacy controller fields via
        ``build_legacy(ctrl)``."""
        plan = getattr(self, "_delivery_plan", None)
        if plan is not None:
            return plan
        ctrl = getattr(self, "_irq_controller", None)
        return build_legacy(ctrl)

    # -- the shared dispatcher --------------------------------------------
    def _apply_pending_irq(self, irq_num: int) -> None:
        """Set up the synthetic exception entry for a pended IRQ. Must run on
        the dispatch thread (between run chunks) — mutating PC/SP is only
        safe while the CPU is not running."""
        arch = self.arch
        if self._prefer_shadow_irq:
            ctrl = getattr(self, "_irq_controller", None)
            if (ctrl is not None
                    and getattr(ctrl, "irq_fired_addr", None) is not None
                    and getattr(ctrl, "irq_number_addr", None) is not None):
                self._apply_pending_irq_shadow(irq_num)
                return
        if arch == "arm":
            self._apply_pending_irq_armv7a(irq_num)
            return
        if arch == "arm64":
            self._apply_pending_irq_arm64(irq_num)
            return
        if arch in ("mips", "powerpc", "powerpc:MPC8XX", "ppc64"):
            self._apply_pending_irq_shadow(irq_num)
            return
        if arch == "x86":
            self._apply_pending_irq_x86(irq_num)
            return
        # Cortex-M (and any un-migrated arch): backend-provided frame push.
        self._apply_cortex_m_fallback(irq_num)

    # -- x86 FRAME delivery -----------------------------------------------
    def _apply_pending_irq_x86(self, irq_num: int) -> None:
        """Deliver an x86 IRQ by building the hardware interrupt frame and
        vectoring at the firmware's IDT stub.

        Resolves the DeliveryPlan the same way every other arch here does
        (``machine.irq_delivery`` first, legacy ``interrupt_controller``
        fields second), and passes ``irq_num`` through, so a plan carrying a
        per-IRQ ``vectors`` map can land IRQ0 and IRQ4 on different stubs.
        A clock ISR learned at run time (VxWorks ``sysClkConnect`` calls the
        controller's ``register_clock_isr``) still wins when the plan has no
        ``isr_addr`` of its own."""
        from halucinator.backends.irq.delivery import (
            DeliveryModel, DeliveryPlan, X86ExceptionDeliverer)

        ctrl = getattr(self, "_irq_controller", None)

        def _legacy(controller):
            return DeliveryPlan(
                model=DeliveryModel.FRAME,
                isr_addr=getattr(controller, "isr_addr", None) if controller else None,
                extra={
                    "int_ent": getattr(controller, "int_ent", None) if controller else None,
                    "int_exit": getattr(controller, "int_exit", None) if controller else None,
                    "stub_addr": getattr(controller, "stub_addr", 0x7000) if controller else 0x7000,
                    "isr_arg": getattr(controller, "isr_arg", 0) if controller else 0,
                },
            )

        plan = self._resolve_delivery_plan(_legacy)
        if plan is None:
            log.warning("inject_irq(%d): x86 has no delivery plan or "
                        "interrupt controller configured; IRQ dropped", irq_num)
            return
        if plan.isr_addr is None and ctrl is not None:
            # Run-time-learned ISR (register_clock_isr).
            plan.isr_addr = getattr(ctrl, "isr_addr", None)
        X86ExceptionDeliverer().deliver(self, irq_num, plan)

    # -- shared SHADOW delivery -------------------------------------------
    def _apply_pending_irq_shadow(self, irq_num: int) -> None:
        """Deliver via shadow-write: write the firmware's post-ack globals
        (irq_number, irq_fired) directly; the main polling loop picks them up
        next iteration. Endianness follows the backend's ``write_memory``."""
        from halucinator.backends.irq.delivery import (
            DeliveryModel, DeliveryPlan, ShadowExceptionDeliverer)

        def _legacy(ctrl):
            return DeliveryPlan(
                model=DeliveryModel.SHADOW,
                irq_fired_addr=(getattr(ctrl, "irq_fired_addr", None)
                                if ctrl else None),
                irq_number_addr=(getattr(ctrl, "irq_number_addr", None)
                                 if ctrl else None),
            )
        ShadowExceptionDeliverer().deliver(self, irq_num,
                                           self._resolve_delivery_plan(_legacy))

    # -- per-arch entries the backend provides/overrides -------------------
    def _apply_pending_irq_armv7a(self, irq_num: int) -> None:
        raise NotImplementedError

    def _apply_pending_irq_arm64(self, irq_num: int) -> None:
        raise NotImplementedError

    # -- dispatch-loop predicate ------------------------------------------
    def in_process_irq_active(self) -> bool:
        """True when an IRQ controller is configured, so main's dispatch loop
        re-enters ``cont()`` after an async IRQ landed the CPU mid-ISR at a
        PC with no registered breakpoint (normal interrupt-driven execution,
        not a derail)."""
        return getattr(self, "_irq_controller", None) is not None
