# Copyright 2026 Christopher Wright

"""Shared snapshot/restore round-trip assertion for the live backend tests
(qemu, libafl-qemu, renode, avatar2, ghidra). Each of those backends uses the
generic HalBackend.save_state/restore_state fallback (reg + writable-RAM
capture); this exercises it end-to-end against the real running backend."""
from __future__ import annotations

from typing import Any

from halucinator.backends.hal_backend import MemoryRegion


def _ensure_ram_region(backend: Any, ram_base: int, ram_size: int) -> None:
    """Make sure a writable, non-emulated RAM region is registered so the
    generic snapshot has something to capture (some live fixtures configure
    memory via the emulator's own config rather than add_memory_region)."""
    regions = getattr(backend, "_regions", None)
    if regions is None:
        backend._regions = regions = []
    covered = any(r.base_addr == ram_base and "w" in r.permissions
                  and not getattr(r, "emulate", None) for r in regions)
    if not covered:
        regions.append(MemoryRegion("snap_ram", ram_base, ram_size, "rw"))


def assert_backend_snapshot_round_trip(backend: Any, ram_base: int,
                                       ram_size: int = 0x1000,
                                       check_registers: bool = True) -> None:
    """Write known RAM (+ optionally a register), snapshot, clobber, restore,
    and assert the machine is back to the snapshot. Works against any
    HalBackend whose save_state/restore_state is the generic reg+RAM fallback.

    ``check_registers=False`` for backends that drop register writes in the
    state the fixture leaves them in (e.g. Renode's GDB stub while paused at
    reset) — the memory round-trip is still fully asserted."""
    _ensure_ram_region(backend, ram_base, ram_size)

    assert backend.can_snapshot() is True

    marker = b"snap-me!"
    backend.write_memory(ram_base + 0x40, 1, marker, len(marker), raw=True)
    if check_registers:
        backend.write_register("r0", 0x1234)

    snap = backend.save_state()
    assert snap.data["mem"], "generic snapshot captured no memory"
    if check_registers:
        assert snap.data["regs"].get("r0") == 0x1234

    backend.write_memory(ram_base + 0x40, 1, b"CLOBBER!", 8, raw=True)
    if check_registers:
        backend.write_register("r0", 0)

    assert backend.restore_state(snap) is True
    assert bytes(backend.read_memory(ram_base + 0x40, 1, len(marker),
                                     raw=True)) == marker
    if check_registers:
        assert backend.read_register("r0") == 0x1234


def assert_restore_rejects_wrong_backend_type(backend: Any) -> None:
    """A snapshot from a different backend type must be refused without
    mutating state."""
    from halucinator.backends.hal_backend import Snapshot
    alien = Snapshot(backend_type="SomeOtherBackend", version=1,
                     data={"regs": {}, "mem": []})
    assert backend.restore_state(alien) is False
