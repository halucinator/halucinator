# Copyright 2026 Christopher Wright
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interrupts must be delivered through the vector table the firmware is
actually using, not the one it booted with.

Firmware relocates SCB->VTOR routinely: a bootloader hands off to an
application with its own table, an RTOS copies the table into RAM so it can
patch it, and a Nordic SoftDevice inserts itself between an MBR at 0x0 and an
application at 0x1c000. Delivering to the reset-time table in any of those
cases is not a near miss -- it vectors into an unrelated binary's handler.
"""
from __future__ import annotations

import pytest

unicorn = pytest.importorskip("unicorn")

from halucinator.backends.hal_backend import MemoryRegion  # noqa: E402
from halucinator.backends.unicorn_backend import UnicornBackend  # noqa: E402


VTOR_ADDR = 0xE000ED08

RESET_TABLE = 0x00000000
RELOCATED_TABLE = 0x00001000
IRQ = 20
# Exception number 16 + 20 = 36, so the slot is at table + 36*4 = table + 0x90.
SLOT = (16 + IRQ) * 4

RESET_HANDLER = 0x00000687          # what the boot table says (Thumb bit set)
RELOCATED_HANDLER = 0x00009679      # what the relocated table says


def _backend():
    be = UnicornBackend(arch="cortex-m3")
    be.add_memory_region(MemoryRegion(
        name="flash", base_addr=0x00000000, size=0x00020000,
        permissions="rwx"))
    be.add_memory_region(MemoryRegion(
        name="sram", base_addr=0x20000000, size=0x00010000,
        permissions="rw-"))
    be.init()
    uc = be._uc
    uc.mem_write(RESET_TABLE + SLOT, RESET_HANDLER.to_bytes(4, "little"))
    uc.mem_write(RELOCATED_TABLE + SLOT, RELOCATED_HANDLER.to_bytes(4, "little"))
    be.write_register("sp", 0x20004000)
    be.write_register("pc", 0x00000100)
    return be


def test_reset_time_table_is_used_when_vtor_is_unset():
    be = _backend()
    be.set_vtor(RESET_TABLE)
    be._apply_pending_irq(IRQ)
    assert be.read_register("pc") == (RESET_HANDLER & ~1)


def test_set_vtor_redirects_delivery():
    """A model that owns the PPB intercepts the firmware's VTOR write, so the
    value never reaches guest memory -- it has to plumb it in via set_vtor."""
    be = _backend()
    be.set_vtor(RESET_TABLE)
    be.set_vtor(RELOCATED_TABLE)
    be._apply_pending_irq(IRQ)
    assert be.read_register("pc") == (RELOCATED_HANDLER & ~1)


def test_vtor_is_read_back_from_guest_memory_when_the_ppb_is_plain_memory():
    """The default case: nothing models the PPB, so the firmware's write lands
    in backend memory and must be honoured without anyone plumbing anything."""
    be = _backend()
    be.set_vtor(RESET_TABLE)
    be._uc.mem_write(VTOR_ADDR, RELOCATED_TABLE.to_bytes(4, "little"))
    assert be._effective_vtor() == RELOCATED_TABLE
    be._apply_pending_irq(IRQ)
    assert be.read_register("pc") == (RELOCATED_HANDLER & ~1)


def test_a_zero_vtor_falls_back_rather_than_believing_it():
    """Zero is both "table at address 0" and "nobody has written this yet".
    Treating it as a relocation would be harmless here but wrong in general;
    the configured base is the better answer and is what reset means anyway."""
    be = _backend()
    be.set_vtor(RELOCATED_TABLE)
    be._uc.mem_write(VTOR_ADDR, (0).to_bytes(4, "little"))
    assert be._effective_vtor() == RELOCATED_TABLE


def test_a_misaligned_vtor_is_not_believed():
    """ARMv7-M requires the table to be aligned to at least 128 bytes. A value
    that is not is not a vector table -- it is a half-written register or a
    catch-all's spin-breaker value, and following it lands anywhere."""
    be = _backend()
    be.set_vtor(RESET_TABLE)
    be._uc.mem_write(VTOR_ADDR, (RELOCATED_TABLE + 4).to_bytes(4, "little"))
    assert be._effective_vtor() == RESET_TABLE


def test_delivery_is_skipped_when_the_slot_is_empty(caplog):
    be = _backend()
    be.set_vtor(RESET_TABLE)
    be._uc.mem_write(RESET_TABLE + SLOT, (0).to_bytes(4, "little"))
    before = be.read_register("pc")
    be._apply_pending_irq(IRQ)
    assert be.read_register("pc") == before
