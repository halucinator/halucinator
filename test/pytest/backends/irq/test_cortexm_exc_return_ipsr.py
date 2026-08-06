"""IPSR must be restored by a Cortex-M exception RETURN, not only cleared.

The backend synthesises M-profile exception entry itself, and on entry it does
write IPSR (``reg_write(UC_ARM_REG_IPSR, exc_num)``). The RETURN path, however,
only ever wrote IPSR when the EXC_RETURN said "return to thread mode"; a
handler-to-handler return (EXC_RETURN 0xFFFFFFF1 / 0xFFFFFFE1, i.e. a nested
exception unwinding into the handler it preempted) relied on
``write_register("cpsr", stacked_xpsr)`` to put the outer exception number
back.

It does not. Unicorn's CPSR write does not touch ``env->v7m.exception`` on an
M-profile core, so after a nested return the CPU keeps reporting the INNER
exception number in IPSR for the whole remaining lifetime of the outer handler.

That is not cosmetic on real firmware:

* rusEFI/FOME calls ``assertInterruptPriority()`` on entry to every handler and
  derives the running exception number from the core, then indexes
  ``NVIC->IP[n]``. A stale IPSR indexes a priority byte nobody ever wrote, the
  handler reads 0 where it wrote 0x40, and the firmware latches
  ``firmwareError("bad isr priority ...")``.
* Any rehost whose interrupt pump refuses to deliver while ``IPSR != 0`` (the
  architecturally correct rule) goes permanently deaf after the first nested
  return: the guest is really back in the outer handler, but from the pump's
  point of view it never leaves the inner one.

Hardware restores the whole xPSR — IPSR included — from the stacked frame.
So does the backend now.
"""
from __future__ import annotations

import struct

import pytest

try:
    import unicorn  # noqa: F401
    from unicorn import arm_const as _A
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

pytestmark = pytest.mark.skipif(not _HAVE_UNICORN,
                                reason="unicorn-engine not installed")

_FLASH = 0x08000000
_RAM = 0x20000000
_VTOR = _FLASH
_OUTER = 5
_INNER = 7


def _make_backend():
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", _FLASH, 0x10000, "rwx"))
    b.add_memory_region(MemoryRegion("ram", _RAM, 0x10000, "rw"))
    b.init()
    b.set_vtor(_VTOR)
    for irq in (_OUTER, _INNER):
        b._uc.mem_write(_VTOR + (16 + irq) * 4,
                        struct.pack("<I", (0x08000200 + 0x40 * irq) | 1))
    b.write_register("sp", _RAM + 0x1000)
    b._uc.reg_write(_A.UC_ARM_REG_IPSR, 0)
    b.write_register("pc", 0x08000100)
    return b


def _ipsr(b) -> int:
    return b._uc.reg_read(_A.UC_ARM_REG_IPSR) & 0x1FF


def test_nested_return_restores_the_outer_exception_number():
    """exc A preempted by exc B; B returns -> IPSR must read A again."""
    b = _make_backend()
    b._apply_pending_irq(_OUTER)
    assert _ipsr(b) == 16 + _OUTER
    b._apply_pending_irq(_INNER)
    assert _ipsr(b) == 16 + _INNER

    exc_ret = b.read_register("lr")
    assert exc_ret & 0x8 == 0, (
        "a nested entry must advertise a return to HANDLER mode, got %#x"
        % exc_ret)
    assert b._maybe_handle_exc_return(exc_ret) is True
    assert _ipsr(b) == 16 + _OUTER, (
        "after the inner exception returned, IPSR still reads %d -- the "
        "stacked xPSR's exception number was never restored" % _ipsr(b))


def test_a_second_injection_is_accepted_after_the_nested_return():
    """The consequence the FOME rehosts hit: a pump that (correctly) refuses to
    inject while IPSR != 0 must be able to inject again once the *outer*
    handler has also returned. With a stale IPSR it never can."""
    b = _make_backend()
    b._apply_pending_irq(_OUTER)
    b._apply_pending_irq(_INNER)
    b._maybe_handle_exc_return(b.read_register("lr"))   # inner returns
    b._maybe_handle_exc_return(b.read_register("lr"))   # outer returns
    assert _ipsr(b) == 0, "back in thread mode, IPSR must be 0"

    # ...and the CPU really can take another exception.
    b._apply_pending_irq(_OUTER)
    assert _ipsr(b) == 16 + _OUTER
    assert b.read_register("pc") == 0x08000200 + 0x40 * _OUTER


def test_thread_return_still_clears_ipsr():
    """The non-nested case must be unchanged."""
    b = _make_backend()
    b._apply_pending_irq(_OUTER)
    b._maybe_handle_exc_return(b.read_register("lr"))
    assert _ipsr(b) == 0


def test_nested_return_does_not_drop_to_thread_on_a_fake_frame():
    """Firmware (ChibiOS' _port_irq_epilogue) synthesises exception frames whose
    stacked xPSR carries only the T bit. A handler-mode EXC_RETURN over such a
    frame must NOT be taken as 'go to thread mode' -- that would silently make
    the running handler look like thread code."""
    b = _make_backend()
    b._apply_pending_irq(_OUTER)
    b._apply_pending_irq(_INNER)
    # Blank the stacked xPSR's exception field, leaving the T bit only.
    sp = b._uc.reg_read(_A.UC_ARM_REG_MSP)
    b._uc.mem_write(sp + 28, struct.pack("<I", 0x01000000))
    b._maybe_handle_exc_return(b.read_register("lr"))
    assert _ipsr(b) != 0, ("a return to handler mode must stay in handler "
                           "mode even when the stacked xPSR is synthetic")
