"""halucinator.backends.irq.delivery — CPU exception delivery (PROTOTYPE).

This module is the "take-the-exception" axis of the IRQ refactor sketch.
It is deliberately separate from the *controllers* in this package:

  * An ``IrqController`` answers "how do I make the line pending?"
    (write NVIC_ISPR / GICD_ISPENDR / CP0 Cause / OpenPIC IPIDR). On a
    real CPU model (QEMU, avatar2) that write is all that's needed — the
    modelled CPU then takes the exception on its own.

  * An ``ExceptionDeliverer`` answers "how does the CPU *enter* the
    handler?" — and only matters for in-process backends (Unicorn,
    Ghidra) whose CPU model does NOT take hardware exceptions. It
    synthesises the architectural exception entry on the dispatch thread.

The two were previously tangled together: ``ArmVicController.deliver()``
and ``UnicornBackend._apply_pending_irq_armv7a()`` were two near-identical
copies of the same ARMv7-A exception-entry sequence, and the controller
also carried firmware-specific data (``isr_addr``, ``irq_simple_entry``).
This module proves that ARM delivery collapses to ONE implementation,
parameterised by a ``DeliveryPlan`` (the "where-to-land" data, which the
OS personality / YAML supplies — not the controller).

PROTOTYPE SCOPE: ARM (A-profile) only. arm64 / mips / ppc / x86 follow
the same shape; they are intentionally not implemented here yet.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)


class DeliveryModel(Enum):
    """How the pended IRQ reaches the handler.

    These recur across arches — they are NOT arch-specific:

      FRAME       synthesise the real architectural exception frame and
                  vector to the handler (ARM IRQ-mode entry at vbar+0x18,
                  x86 IDT frame, arm64 VBAR_EL1 entry).
      TRAMPOLINE  AAPCS-style call into a firmware stub: LR/return = the
                  interrupted PC, jump to the trampoline, plain ``ret``.
      SHADOW      write the post-ack globals (irq_fired / irq_number) the
                  firmware polls; no ISR ever runs (used on mips/ppc).
    """
    FRAME = "frame"
    TRAMPOLINE = "trampoline"
    SHADOW = "shadow"


@dataclass
class DeliveryPlan:
    """The "where-to-land" axis — pure data, owned by the backend and
    populated from YAML and/or an OS personality (e.g. the VxWorks
    ``sysClkConnect`` bp_handler fills ``isr_addr`` at run time).

    A controller never holds these: they are firmware/OS facts, not
    interrupt-controller facts.
    """
    model: DeliveryModel = DeliveryModel.FRAME
    # FRAME: base of the exception vector table (ARM SCTLR.V==0 -> 0x0).
    vector_base: int = 0x0
    # FRAME fallback / TRAMPOLINE: the connected ISR, learned or configured.
    isr_addr: Optional[int] = None
    # TRAMPOLINE: an AAPCS trampoline that ends in `mov pc, lr` / `bx lr`.
    trampoline: Optional[int] = None
    # FRAME (GIC only): CPU-interface base. When set, the deliverer stashes
    # the acknowledged IRQ id into GICC_IAR so the firmware ISR reads the
    # right number (the in-process backend models no real GIC CPU iface).
    gicc_base: Optional[int] = None
    # SHADOW: firmware globals the deliverer writes (irq fired flag + number)
    # so a polling firmware loop sees the IRQ without any ISR running.
    irq_fired_addr: Optional[int] = None
    irq_number_addr: Optional[int] = None
    # Arch-specific delivery data not yet modelled by a dedicated field
    # (e.g. x86 int_ent/int_exit/stub_addr, mips *_phys_addr). Carried
    # losslessly so the back-compat shim never drops a configured value;
    # an arch deliverer reads what it needs from here.
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_block(cls, block: Dict[str, Any]) -> "DeliveryPlan":
        """Parse an explicit `machine.irq_delivery` YAML block.

        `model` is optional — if omitted it's inferred from the fields
        present (same rule the old config relied on implicitly), so a
        minimal block still works.
        """
        if not isinstance(block, dict):
            raise ValueError(
                f"machine.irq_delivery must be a mapping, got "
                f"{type(block).__name__}"
            )
        raw_model = block.get("model")
        if raw_model is not None:
            try:
                model = DeliveryModel(raw_model)
            except ValueError:
                valid = ", ".join(m.value for m in DeliveryModel)
                raise ValueError(
                    f"machine.irq_delivery.model={raw_model!r} invalid; "
                    f"one of: {valid}"
                ) from None
        else:
            model = _infer_model(block)
        extra = {k: v for k, v in block.items() if k not in _PLAN_TYPED_KEYS}
        return cls(
            model=model,
            vector_base=block.get("vector_base", 0x0),
            isr_addr=block.get("isr_addr"),
            trampoline=block.get("trampoline"),
            gicc_base=block.get("gicc_base"),
            irq_fired_addr=block.get("irq_fired_addr"),
            irq_number_addr=block.get("irq_number_addr"),
            extra=extra,
        )

    @classmethod
    def from_legacy_controller(
        cls, ctrl: Dict[str, Any],
    ) -> Optional["DeliveryPlan"]:
        """Back-compat shim: derive a DeliveryPlan from an OLD-style
        `interrupt_controller` block that carried firmware/synth fields
        (isr_addr, irq_simple_entry, int_ent/int_exit, irq_fired_addr…).

        Returns None when the controller block is purely hardware (no
        synth fields) — those targets have a real CPU model and need no
        deliverer. Callers should emit a deprecation warning when this
        returns non-None.
        """
        if not isinstance(ctrl, dict):
            return None
        # Old arm_vic/x86_pic blocks nested firmware fields under
        # `options:`; arm32/mips put them top-level. Flatten both, with
        # top-level winning, into one view.
        opts = ctrl.get("options") or {}
        flat = {**opts, **{k: v for k, v in ctrl.items() if k != "options"}}
        if not any(flat.get(k) is not None for k in _SYNTH_FIRMWARE_KEYS):
            return None
        # `irq_simple_entry` was the old name for `trampoline`.
        trampoline = flat.get("trampoline", flat.get("irq_simple_entry"))
        if trampoline is not None:
            flat["trampoline"] = trampoline
        model = _infer_model(flat)
        # Everything that isn't a controller-hardware key or a typed plan
        # field is arch-specific delivery data → preserve in extra.
        ctrl_hw_keys = {"type", "gicd_base", "openpic_base", "irq_simple_entry"}
        extra = {
            k: v for k, v in flat.items()
            if k not in _PLAN_TYPED_KEYS and k not in ctrl_hw_keys
        }
        return cls(
            model=model,
            vector_base=flat.get("vector_base", 0x0),
            isr_addr=flat.get("isr_addr"),
            trampoline=trampoline,
            gicc_base=flat.get("gicc_base"),
            irq_fired_addr=flat.get("irq_fired_addr"),
            irq_number_addr=flat.get("irq_number_addr"),
            extra=extra,
        )


# Controller `type`s that, for an in-process backend, imply the firmware
# fields on the controller block were really delivery config (the
# back-compat case the shim rewrites into a DeliveryPlan).
_SYNTH_FIRMWARE_KEYS = (
    "isr_addr", "irq_simple_entry", "trampoline",
    "irq_fired_addr", "irq_number_addr",
    "int_ent", "int_exit", "stub_addr", "isr_arg",
    "irq_fired_phys_addr", "irq_number_phys_addr",
)
# Keys consumed into typed DeliveryPlan fields; everything else a synth
# controller block carries (and isn't a controller-hardware key) flows to
# `extra` so nothing is silently dropped.
_PLAN_TYPED_KEYS = {
    "model", "vector_base", "isr_addr", "trampoline", "gicc_base",
    "irq_fired_addr", "irq_number_addr",
}


def _infer_model(d: Dict[str, Any]) -> DeliveryModel:
    """Pick the delivery model implied by which fields are present.

    Mirrors the pre-refactor implicit coupling: a trampoline address meant
    'AAPCS call', shadow globals meant 'poll, no ISR', otherwise a
    synthesised exception frame."""
    if d.get("trampoline") is not None or d.get("irq_simple_entry") is not None:
        return DeliveryModel.TRAMPOLINE
    if d.get("irq_fired_addr") is not None or d.get("irq_number_addr") is not None:
        return DeliveryModel.SHADOW
    return DeliveryModel.FRAME


class ExceptionDeliverer(ABC):
    """Per-arch CPU exception synthesis for backends without a real CPU
    exception model. Runs ONLY on the dispatch thread (the caller owns the
    threading discipline — Unicorn is not safe against PC/CPSR writes
    mid-``emu_start``)."""

    arch: str = "abstract"

    @abstractmethod
    def deliver(self, backend: "HalBackend", num: int,
                plan: DeliveryPlan) -> bool:
        """Synthesise the exception entry for IRQ ``num``.

        Returns True if the entry was set up (PC now at the handler),
        False if delivery was suppressed (e.g. IRQs masked). The caller
        decides whether a suppressed tick is dropped or re-queued.
        """


# ---------------------------------------------------------------------------
# ARM (A-profile, ARMv5/v6/v7-A)
# ---------------------------------------------------------------------------

# CPSR mode + flag bits.
_ARM_MODE_IRQ = 0x12
_ARM_MODE_MASK = 0x1F
_ARM_CPSR_I = 0x80   # IRQ disable (mask) bit
_ARM_CPSR_T = 0x20   # Thumb-state bit
_IRQ_VECTOR_OFFSET = 0x18
_GICC_IAR_OFFSET = 0x0C


class ArmExceptionDeliverer(ExceptionDeliverer):
    """Synthesised A-profile-ARM IRQ entry — the SINGLE implementation
    that replaces both ``ArmVicController.deliver`` (the VIC / synth path)
    and ``UnicornBackend._apply_pending_irq_armv7a`` (the GIC / built-in
    path). They were the same exception-entry sequence with two different
    target-selection policies and one extra GICC_IAR shadow write; both
    are expressed here as data on the ``DeliveryPlan``.

    Architectural IRQ entry (ARM ARM B1.8.3)::

        R14_irq (LR_irq) = interrupted PC + 4   (handler does `subs pc,lr,#4`)
        SPSR_irq         = CPSR (pre-exception)
        CPSR.M           = 0b10010 (IRQ mode)
        CPSR.I           = 1       (mask further IRQs)
        CPSR.T           = 0       (ARM state)
        PC               = target  (see _select_target)
    """

    arch = "arm"

    def deliver(self, backend: "HalBackend", num: int,
                plan: DeliveryPlan) -> bool:
        # Let OS bp_handlers (e.g. IntLvlVecChkArm) read back which IRQ fired.
        setattr(backend, "_last_delivered_irq", int(num))

        cpsr = backend.read_register("cpsr")
        if cpsr & _ARM_CPSR_I:
            # IRQs masked. Suppress; the caller's policy (drop vs re-queue)
            # decides what happens to this tick.
            return False

        pc = backend.read_register("pc")
        target = self._select_target(backend, plan)

        # Switch to IRQ mode (writing CPSR auto-banks SP/LR/SPSR in unicorn).
        new_cpsr = cpsr & ~(_ARM_MODE_MASK | _ARM_CPSR_T)
        new_cpsr |= _ARM_MODE_IRQ | _ARM_CPSR_I
        backend.write_register("cpsr", new_cpsr)

        # Now in the IRQ-banked LR/SPSR.
        backend.write_register("lr", (pc + 4) & 0xFFFFFFFF)
        backend.write_register("spsr", cpsr)

        # GIC path only: stash the acknowledged id into the GICC_IAR shadow
        # so the firmware ISR reads the right interrupt number. Absent on
        # the VIC path (plan.gicc_base is None) — exactly matching old
        # ArmVicController.deliver, which never touched GICC_IAR.
        if plan.gicc_base is not None:
            backend.write_memory(plan.gicc_base + _GICC_IAR_OFFSET, 4,
                                 int(num) & 0xFFFFFFFF)

        backend.write_register("pc", target & 0xFFFFFFFF)
        return True

    def _select_target(self, backend: "HalBackend",
                       plan: DeliveryPlan) -> int:
        """Resolve the entry PC. Encodes the union of both old policies:

          * VIC path: trampoline > (isr_addr if vectors not installed) >
            vector_base+0x18
          * GIC/built-in path: always vector_base+0x18

        The GIC path is just the VIC path with trampoline/isr_addr unset.
        """
        if plan.model is DeliveryModel.TRAMPOLINE and plan.trampoline is not None:
            return plan.trampoline
        if (plan.isr_addr is not None
                and not self._vector_installed(backend, plan.vector_base)):
            return plan.isr_addr
        return plan.vector_base + _IRQ_VECTOR_OFFSET

    @staticmethod
    def _vector_installed(backend: "HalBackend", vector_base: int) -> bool:
        """Heuristic: non-zero word at vector_base+0x18 means the firmware
        installed its real IRQ vector (typically `ldr pc,[pc,#off]`)."""
        try:
            word = backend.read_memory(vector_base + _IRQ_VECTOR_OFFSET, 4, 1)
        except Exception:  # noqa: BLE001
            return False
        return int(word) != 0


# ---------------------------------------------------------------------------
# AArch64
# ---------------------------------------------------------------------------

_A64_VECTOR_OFFSET = 0x280   # VBAR_EL1 + 0x280 = current-EL-SPx IRQ vector


class Arm64ExceptionDeliverer(ExceptionDeliverer):
    """AArch64 IRQ entry for in-process unicorn (replaces
    ``UnicornBackend._apply_pending_irq_arm64``).

    Unicorn's ARM64 model doesn't fully implement EL1 vector delivery +
    ERET, so the firmware exposes an AAPCS ``_irq_entry_simple``
    trampoline (LR = interrupted PC, plain ``ret``). When no trampoline is
    configured we fall back to the architectural VBAR_EL1 + 0x280 vector
    (documented hook; Unicorn may not honour ERET on return)."""

    arch = "arm64"

    def deliver(self, backend: "HalBackend", num: int,
                plan: DeliveryPlan) -> bool:
        setattr(backend, "_last_delivered_irq", int(num))
        # GICC_IAR shadow so the firmware ISR reads the right id.
        if plan.gicc_base is not None:
            try:
                backend.write_memory(plan.gicc_base + _GICC_IAR_OFFSET, 4,
                                     int(num) & 0xFFFFFFFF)
            except Exception:  # noqa: BLE001
                pass

        target = plan.trampoline
        if target is None and plan.model is DeliveryModel.TRAMPOLINE:
            target = plan.isr_addr
        if target is not None:
            return_pc = backend.read_register("pc")
            backend.write_register("lr", return_pc)
            backend.write_register("pc", int(target))
            return True

        # FRAME fallback: real-CPU vector path.
        try:
            vbar = backend.read_register("vbar_el1")
        except Exception:  # noqa: BLE001
            vbar = plan.vector_base
        return_pc = backend.read_register("pc")
        try:
            backend.write_register("elr_el1", return_pc)
        except Exception:  # noqa: BLE001
            pass
        backend.write_register("pc", vbar + _A64_VECTOR_OFFSET)
        log.warning("arm64: IRQ %d vector entry at 0x%x — Unicorn may not "
                    "honour ERET on return", num, vbar + _A64_VECTOR_OFFSET)
        return True


# ---------------------------------------------------------------------------
# SHADOW (MIPS / PowerPC) — write post-ack globals, no ISR runs
# ---------------------------------------------------------------------------

class ShadowExceptionDeliverer(ExceptionDeliverer):
    """SHADOW delivery for in-process backends whose CPU model can't take
    the arch exception reliably (MIPS CP0 EBase+0x180, PPC SRR0/SRR1).
    Replaces the near-identical ``_apply_pending_irq_mips`` and
    ``_apply_pending_irq_ppc``.

    Writes the firmware's post-ack globals (irq_number = N, irq_fired = 1)
    directly; the firmware's polling loop sees the change with no ISR ever
    running. Word endianness follows the backend (``write_memory`` uses the
    arch's byte order), so the same code serves big-endian MIPS/PPC and a
    little-endian shadow target alike."""

    arch = "shadow"

    def deliver(self, backend: "HalBackend", num: int,
                plan: DeliveryPlan) -> bool:
        setattr(backend, "_last_delivered_irq", int(num))
        if plan.irq_number_addr is None or plan.irq_fired_addr is None:
            log.warning("shadow: no irq_fired_addr/irq_number_addr — IRQ %d "
                        "will not be delivered to the firmware", num)
            return False
        ok = backend.write_memory(int(plan.irq_number_addr), 4,
                                  int(num) & 0xFFFFFFFF)
        ok = backend.write_memory(int(plan.irq_fired_addr), 4, 1) and ok
        if ok:
            log.info("shadow: IRQ %d -> irq_number@0x%x irq_fired@0x%x",
                     num, plan.irq_number_addr, plan.irq_fired_addr)
        return ok


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_DELIVERER_CLASSES = {
    "arm": ArmExceptionDeliverer,
    "arm64": Arm64ExceptionDeliverer,
    "mips": ShadowExceptionDeliverer,
    "powerpc": ShadowExceptionDeliverer,
    "powerpc:MPC8XX": ShadowExceptionDeliverer,
    "ppc64": ShadowExceptionDeliverer,
}


def build_exception_deliverer(arch: str) -> Optional[ExceptionDeliverer]:
    """Return the ExceptionDeliverer for `arch`, or None when that arch's
    backend takes exceptions natively (cortex-m's NVIC fast-path, QEMU's
    real CPU model) and needs no in-process synthesis."""
    cls = _DELIVERER_CLASSES.get(arch)
    return cls() if cls is not None else None


__all__ = [
    "DeliveryModel",
    "DeliveryPlan",
    "ExceptionDeliverer",
    "ArmExceptionDeliverer",
    "Arm64ExceptionDeliverer",
    "ShadowExceptionDeliverer",
    "build_exception_deliverer",
]
