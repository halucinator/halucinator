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
from halucinator import hal_log  # noqa: E402
hlog = hal_log.getHalLogger()


class InProcessIrqMixin:
    # Backends that deliver via a firmware-side shadow write (Ghidra) set
    # this True to prefer the shadow path whenever the controller carries
    # shadow addresses, even for arm/arm64 (it sidesteps banked-register
    # quirks). Unicorn leaves it False and uses the per-arch entries.
    _prefer_shadow_irq: bool = False

    # -- Cortex-M EXC_RETURN -----------------------------------------------
    # A PC whose top nibble matches EXC_RETURN_MAGIC is an ISR doing `bx lr`.
    _EXC_RETURN_THREAD_MSP = 0xFFFFFFF9
    _EXC_RETURN_MASK = 0xFFFFFFF0
    _EXC_RETURN_MAGIC = 0xFFFFFFF0

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
        if arch == "m68k":
            self._apply_pending_irq_m68k(irq_num)
            return
        if arch == "x86":
            # x86 delivery lives in X86ExceptionDeliverer; the configured
            # X86PicController.deliver is a thin shim over it that carries the
            # runtime-learned clock ISR. Runs on the dispatch thread here, so
            # mutating EIP/ESP is safe.
            ctrl = getattr(self, "_irq_controller", None)
            if ctrl is not None and hasattr(ctrl, "deliver"):
                ctrl.deliver(self)
            else:
                log.warning("inject_irq(%d): x86 has no X86PicController "
                            "configured; tick dropped", irq_num)
            return
        # Cortex-M (and any un-migrated arch): backend-provided frame push.
        self._apply_cortex_m_fallback(irq_num)

    # -- m68k / ColdFire ---------------------------------------------------
    def _apply_pending_irq_m68k(self, irq_num: int) -> None:
        """Synthesise an m68k exception entry for *irq_num* (a VECTOR NUMBER).

        The 68000 family takes an exception by pushing a frame on the
        SUPERVISOR stack, loading the handler address from ``VBR + vector*4``,
        and entering supervisor state with the interrupt level raised. unicorn
        performs none of that (its m68k CPU has no exception machinery), so we
        do it here, on the dispatch thread, where mutating PC/SP is safe.

        ColdFire uses a 2-longword frame, which is what we push:

            SP+0 : FORMAT[31:28] | FS[27:26] | VECTOR[25:18] | FS[17:16] | SR[15:0]
            SP+4 : PC

        Two m68k-specific hazards, both of which cost real debugging time:

        * **A7 is BANKED.** It is the user stack pointer while SR.S is clear and
          the supervisor stack pointer while it is set. Every A7 access below is
          therefore ordered so the supervisor bank is the live one: we raise
          SR.S *before* touching A7 on entry, and decrement A7 *before*
          restoring SR on exit (see ``_m68k_handle_rte`` in the backend).
        * **The vector is a NUMBER, not an address.** Autovectored interrupt
          level *n* is vector ``24 + n``; device interrupts on a ColdFire INTC
          use vectors 64 and up. Callers pass the vector number.
        """
        uc_regs = self.regs
        vector = int(irq_num) & 0xFF

        # --- carry the CONDITION CODES across the exception ---------------
        # This snapshot MUST be the very first thing done here -- before even
        # READING SR. unicorn cannot expose the m68k CCR (QEMU evaluates flags
        # lazily via cc_op/cc_dest), and both reading and writing SR flush that
        # lazy state to zero. So by the time `sr = uc_regs.sr` has run the
        # guest's condition codes are already gone, and an interrupt landing
        # between a compare and its branch would silently send the firmware
        # down the wrong path.
        #
        # context_save() captures the whole CPUState including the lazy flag
        # fields; _m68k_handle_rte transplants them back. Stacked, so nested
        # exceptions unwind in order.
        stack = getattr(self, "_m68k_ctx_stack", None)
        if stack is None:
            stack = self._m68k_ctx_stack = []
        try:
            _ctx = self._uc.context_save()
        except Exception as exc:  # noqa: BLE001
            _ctx = None
            hlog.warning("m68k: context_save failed (%s) -- condition codes "
                         "will NOT survive this exception", exc)

        sr = uc_regs.sr
        pc = uc_regs.pc

        # --- HONOUR THE INTERRUPT MASK -----------------------------------
        # SR[10:8] is the interrupt priority level. Real 68k hardware delivers
        # an interrupt only if its level is GREATER than the current IPL;
        # level 7 is non-maskable. Ignoring this delivers interrupts into code
        # that has explicitly masked them -- e.g. a reset stub running at
        # IPL 7, or any critical section -- which corrupts firmware that is
        # correct on real silicon. Autovectors 25..31 encode their own level
        # (vector 24 + n); device vectors (64+) take their level from the
        # interrupt controller, which is device-specific, so it is configurable
        # here and defaults to 4 (a mid priority a firmware at IPL 0 accepts).
        if 25 <= vector <= 31:
            level = vector - 24
        else:
            # HAL_M68K_IRQ_LEVEL takes either a bare default ("4") or a
            # per-vector map ("64:6,80:3,*:4"). Per-vector matters: a system
            # tick must be able to PREEMPT a lower-priority software-yield
            # ISR, and with one shared level the yield handler (running at
            # that level) masks the tick permanently -- the RTOS then runs but
            # never advances time.
            spec = os.environ.get("HAL_M68K_IRQ_LEVEL", "4")
            level = 4
            if ":" in spec:
                default = 4
                for tok in spec.split(","):
                    tok = tok.strip()
                    if not tok or ":" not in tok:
                        continue
                    key, _, val = tok.partition(":")
                    try:
                        lvl = int(val, 0)
                    except ValueError:
                        continue
                    if key.strip() == "*":
                        default = lvl
                    elif key.strip().isdigit() and int(key) == vector:
                        default = lvl
                        break
                level = default
            else:
                try:
                    level = int(spec, 0)
                except ValueError:
                    level = 4
            level = min(max(level, 1), 7)
        cur_ipl = (sr >> 8) & 0x7
        if level != 7 and level <= cur_ipl:
            # Masked. Drop it, exactly as the hardware would -- and say so once
            # per (vector, IPL) pair, because "my timer never fires" with no
            # diagnostic is the worst possible failure mode here.
            key = (vector, cur_ipl)
            seen = getattr(self, "_m68k_masked_seen", None)
            if seen is None:
                seen = self._m68k_masked_seen = set()
            if key not in seen:
                seen.add(key)
                hlog.info("m68k: vector %d (level %d) MASKED -- firmware is at "
                          "IPL %d. The firmware must lower SR.IPL before this "
                          "interrupt can be delivered.", vector, level, cur_ipl)
            # A real interrupt controller HOLDS the request asserted until it
            # is serviced -- it does not discard it because the CPU happened to
            # be masked. Dropping it here deadlocks level-triggered firmware:
            # FreeRTOS's vPortEnterCritical spins until MCF_INTC0_INTFRCL reads
            # 0, which only the yield ISR clears, so a yield dropped while the
            # tick ISR held a higher IPL wedges the kernel forever. Re-queue it
            # for the next chunk instead. (Held in a SEPARATE list: appending to
            # _pending_irqs here would spin the caller's drain loop.)
            deferred = getattr(self, "_m68k_masked_pending", None)
            if deferred is None:
                deferred = self._m68k_masked_pending = []
            if vector not in deferred:
                deferred.append(vector)
            # Reading SR above already flushed the guest's condition codes, so
            # put them back or a MASKED interrupt corrupts the firmware just as
            # badly as a delivered one.
            if _ctx is not None:
                try:
                    self._uc.context_restore(_ctx)
                except Exception:  # noqa: BLE001
                    pass
            return

        stack.append(_ctx)

        # Enter supervisor FIRST so A7 refers to the supervisor stack.
        new_sr = (sr | 0x2000) & ~0x0700          # S = 1, clear IPL...
        new_sr |= (level & 0x7) << 8              # ...then raise to this level
        uc_regs.sr = new_sr

        sp = uc_regs.sp - 8
        fmt_word = ((0x4 << 28) | ((vector & 0xFF) << 18) | (sr & 0xFFFF))
        self.write_memory(sp, 4, fmt_word, num_words=1)
        self.write_memory(sp + 4, 4, pc, num_words=1)
        uc_regs.sp = sp

        # VBR relocates the vector table on real hardware, but unicorn 2.1.4
        # does NOT implement the m68k VBR control register: reads are a no-op
        # (register id 21) and emit a deprecation warning, and `movec ax,%vbr`
        # does not stick. So the table is only reachable at address 0. Probe
        # once, cache the answer, and say so ONCE -- a firmware that relocates
        # its vector table (an RTOS moving it to RAM) will not work until
        # unicorn implements VBR, and that must not be a silent wrong answer.
        vbr = 0
        if not getattr(self, "_m68k_vbr_checked", False):
            self._m68k_vbr_checked = True
            self._m68k_vbr_usable = False
            # Probe by WRITE-THEN-READ-BACK. A plain read returns 0 rather
            # than None on a no-op register, so "did the read succeed" cannot
            # distinguish "VBR is 0" from "VBR is unimplemented" -- and getting
            # that wrong means re-reading an unimplemented register on every
            # single delivery (and re-emitting unicorn's deprecation warning).
            try:
                _orig = uc_regs.vbr
                uc_regs.vbr = 0x0BAD0000
                self._m68k_vbr_usable = (uc_regs.vbr == 0x0BAD0000)
                uc_regs.vbr = _orig
            except Exception:  # noqa: BLE001
                self._m68k_vbr_usable = False
            if not self._m68k_vbr_usable:
                log.warning(
                    "m68k: unicorn does not implement VBR -- assuming the "
                    "exception vector table is at address 0. Firmware that "
                    "RELOCATES its vector table will vector incorrectly.")
        if getattr(self, "_m68k_vbr_usable", False):
            try:
                vbr = uc_regs.vbr or 0
            except Exception:  # noqa: BLE001
                vbr = 0

        handler = self.read_memory(vbr + vector * 4, 4, 1)
        if not handler:
            log.warning("inject_irq(vector %d): vector table entry at "
                        "0x%08x is 0 -- refusing to jump to NULL",
                        vector, vbr + vector * 4)
            # Undo the frame push so the firmware is left untouched.
            uc_regs.sp = sp + 8
            uc_regs.sr = sr
            return
        uc_regs.pc = handler

        hlog.info("m68k exception: vector %d -> handler 0x%08x "
                 "(saved pc=0x%08x sr=0x%04x, sp=0x%08x)",
                 vector, handler, pc, sr, sp)

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
