"""Unit tests for SCB->ICSR maintenance across Cortex-M exception entry/return.

This backend delivers M-profile exceptions itself and leaves the private
peripheral bus as plain RW memory, so before this was added nothing maintained
ICSR: VECTACTIVE and RETTOBASE read 0 forever.

RETTOBASE reading 0 is not cosmetic. ChibiOS' ARMv7-M ISR epilogue is

    ldr  r3, [SCB_ICSR]
    ands r3, #0x800          @ RETTOBASE
    beq  no_reschedule

so a stuck-at-zero RETTOBASE makes the kernel skip the deferred context switch
on every interrupt: the tick runs, virtual timers expire, threads are readied --
and the CPU returns to the idle thread, forever. Firmware that asks "am I in an
interrupt?" via VECTACTIVE rather than IPSR is wrong the same silent way.
"""
from __future__ import annotations

import struct

import pytest

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False

pytestmark = pytest.mark.skipif(not _HAVE_UNICORN,
                                reason="unicorn-engine not installed")

_FLASH = 0x08000000
_RAM = 0x20000000
_VTOR = _FLASH
_IRQ = 5
_HANDLER = 0x08000200
_ICSR = 0xE000ED04
_VECTACTIVE = 0x1FF
_RETTOBASE = 1 << 11


def _make_backend():
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", _FLASH, 0x10000, "rwx"))
    b.add_memory_region(MemoryRegion("ram", _RAM, 0x10000, "rw"))
    b.init()
    b.set_vtor(_VTOR)
    b._uc.mem_write(_VTOR + (16 + _IRQ) * 4, struct.pack("<I", _HANDLER | 1))
    b.write_register("sp", _RAM + 0x1000)
    b._uc.reg_write(unicorn.arm_const.UC_ARM_REG_IPSR, 0)   # thread mode
    b.write_register("pc", 0x08000100)
    return b


def _icsr(b) -> int:
    return int.from_bytes(b._uc.mem_read(_ICSR, 4), "little")


def test_icsr_zero_before_any_exception():
    b = _make_backend()
    assert _icsr(b) == 0


def test_entry_sets_vectactive_and_rettobase():
    """Taking an exception from thread mode: VECTACTIVE = the exception number,
    RETTOBASE = 1 (nothing else is active, so returning goes to base level)."""
    b = _make_backend()
    b._apply_pending_irq(_IRQ)
    icsr = _icsr(b)
    assert icsr & _VECTACTIVE == 16 + _IRQ
    assert icsr & _RETTOBASE, "RETTOBASE must be set for a non-nested exception"


def test_nested_exception_clears_rettobase():
    """A second exception taken while one is already active must NOT report
    RETTOBASE: returning from it goes back to the preempted handler."""
    b = _make_backend()
    b._apply_pending_irq(_IRQ)
    b._uc.mem_write(_VTOR + (16 + 7) * 4, struct.pack("<I", (_HANDLER + 0x40) | 1))
    b._apply_pending_irq(7)
    icsr = _icsr(b)
    assert icsr & _VECTACTIVE == 16 + 7
    assert not (icsr & _RETTOBASE)


def test_return_to_thread_clears_vectactive():
    """Unwinding the frame back to thread mode leaves VECTACTIVE == 0."""
    b = _make_backend()
    b._apply_pending_irq(_IRQ)
    exc_ret = b.read_register("lr")
    assert b._maybe_handle_exc_return(exc_ret) is True
    icsr = _icsr(b)
    assert icsr & _VECTACTIVE == 0
    assert icsr & _RETTOBASE


def test_firmware_owned_icsr_bits_are_preserved():
    """VECTACTIVE/RETTOBASE are ours; PENDSVSET and friends belong to the
    firmware and must survive an exception entry untouched."""
    b = _make_backend()
    b._uc.mem_write(_ICSR, struct.pack("<I", 1 << 28))     # PENDSVSET
    b._apply_pending_irq(_IRQ)
    icsr = _icsr(b)
    assert icsr & (1 << 28), "PENDSVSET was clobbered by the ICSR update"
    assert icsr & _VECTACTIVE == 16 + _IRQ
