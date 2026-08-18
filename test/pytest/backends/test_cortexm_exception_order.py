# Copyright 2026 Christopher Wright
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A synchronous exception must be taken before an asynchronous one.

The pending queue mixes two different kinds of thing. An interrupt is
asynchronous and may be taken between any two instructions, so stacking
whatever PC the CPU is at is always right. A `svc` is synchronous and precise:
the instruction has already executed, so the frame must stack the address
immediately after it. Taking an interrupt first breaks that, and every ARMv7-M
supervisor-call dispatcher recovers the call number from `stacked_PC - 2`.
"""
from __future__ import annotations

import struct

import pytest

unicorn = pytest.importorskip("unicorn")

from halucinator.backends.hal_backend import MemoryRegion  # noqa: E402
from halucinator.backends.unicorn_backend import UnicornBackend  # noqa: E402

VECTORS = 0x00000000
SVCALL_HANDLER = 0x00000901
IRQ20_HANDLER = 0x00000687
SVC_SITE = 0x00002032           # where the `svc` "was"
AFTER_SVC = SVC_SITE + 2


def _backend():
    be = UnicornBackend(arch="cortex-m3")
    be.add_memory_region(MemoryRegion(
        name="flash", base_addr=0x00000000, size=0x00020000, permissions="rwx"))
    be.add_memory_region(MemoryRegion(
        name="sram", base_addr=0x20000000, size=0x00010000, permissions="rw-"))
    be.init()
    uc = be._uc
    uc.mem_write(VECTORS + 11 * 4, struct.pack("<I", SVCALL_HANDLER))
    uc.mem_write(VECTORS + (16 + 20) * 4, struct.pack("<I", IRQ20_HANDLER))
    be.set_vtor(VECTORS)
    be.write_register("sp", 0x20004000)
    be.write_register("pc", AFTER_SVC)          # as after an executed `svc`
    return be


def _stacked_pc(be):
    """The PC in the exception frame the most recent entry pushed."""
    sp = be.read_register("sp")
    return struct.unpack("<I", bytes(be._uc.mem_read(sp + 24, 4)))[0]


def test_svcall_is_applied_before_a_queued_interrupt():
    be = _backend()
    be._pending_irqs.extend([20, be._SVCALL_IRQ])   # interrupt queued first
    be._drain_pending_irqs()
    # SVCall must have been taken first, so ITS frame carries the post-svc PC.
    # The interrupt is then taken on top, stacking the SVCall handler entry.
    assert _stacked_pc(be) == SVCALL_HANDLER & ~1
    assert be.read_register("pc") == IRQ20_HANDLER & ~1


def test_the_svcall_frame_carries_the_address_after_the_svc():
    """This is the property a dispatcher depends on: it reads stacked_PC - 2 to
    get the `svc` opcode and its immediate."""
    be = _backend()
    be._pending_irqs.extend([20, be._SVCALL_IRQ])
    # Take only the SVCall by draining with nothing else queued afterwards.
    be._pending_irqs = [be._SVCALL_IRQ]
    be._drain_pending_irqs()
    assert _stacked_pc(be) == AFTER_SVC


def test_taking_the_interrupt_first_would_corrupt_the_svc_number():
    """The bug this ordering prevents, demonstrated: apply them in the wrong
    order and the SVCall frame stacks the interrupt handler's entry address, so
    stacked_PC - 2 lands in unrelated code."""
    be = _backend()
    be._apply_pending_irq(20)
    be._apply_pending_irq(be._SVCALL_IRQ)
    assert _stacked_pc(be) == IRQ20_HANDLER & ~1     # NOT AFTER_SVC
    assert _stacked_pc(be) != AFTER_SVC


def test_interrupts_alone_drain_in_order():
    be = _backend()
    be._pending_irqs.extend([20])
    be._drain_pending_irqs()
    assert be.read_register("pc") == IRQ20_HANDLER & ~1
    assert be._pending_irqs == []


# --- the chunk hook --------------------------------------------------------
def test_chunk_hooks_run_and_can_be_registered_once():
    be = _backend()
    calls = []
    be.add_chunk_hook(lambda: calls.append(1))
    be._run_chunk_hooks()
    be._run_chunk_hooks()
    assert calls == [1, 1]


def test_a_hook_registered_twice_runs_once():
    be = _backend()
    calls = []

    def hook():
        calls.append(1)

    be.add_chunk_hook(hook)
    be.add_chunk_hook(hook)
    be._run_chunk_hooks()
    assert calls == [1]


def test_a_failing_hook_cannot_abort_the_run():
    be = _backend()
    calls = []
    be.add_chunk_hook(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    be.add_chunk_hook(lambda: calls.append(1))
    be._run_chunk_hooks()                        # must not raise
    assert calls == [1]
