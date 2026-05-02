"""Tests that State_Recorder works against any HalBackend — not just
avatar2's QemuTarget."""
import os
import sqlite3
import tempfile
from unittest import mock

import pytest


@pytest.fixture
def tmp_db_and_elf(tmp_path):
    db = str(tmp_path / "recorder.sqlite")
    elf = tmp_path / "fw.elf"
    elf.write_bytes(b"\x7fELF\x01\x01\x01" + b"\x00" * 50)
    return db, str(elf)


def _make_fake_backend(register_names=("r0", "r1", "sp", "pc")):
    """A mock HalBackend that implements the subset of the API
    State_Recorder uses."""
    backend = mock.MagicMock()
    backend.list_registers = mock.Mock(return_value=list(register_names))
    backend.read_register = mock.Mock(side_effect=lambda name: {
        "r0": 0x100, "r1": 0x200, "sp": 0x20008000, "pc": 0x08000100,
    }.get(name, 0))
    backend.read_memory = mock.Mock(return_value=b"\xAA" * 16)
    backend.set_breakpoint = mock.Mock(return_value=42)
    backend.remove_breakpoint = mock.Mock()
    # The recorder accesses regs.lr / regs.pc via backend.regs proxy.
    backend.regs.lr = 0x08000123
    backend.regs.pc = 0x08000100
    return backend


def test_get_state_uses_list_registers_on_halbackend(tmp_db_and_elf):
    from halucinator.util.profile_hals import State_Recorder

    db, elf = tmp_db_and_elf
    backend = _make_fake_backend()
    memories = [(0x20000000, 0x100)]
    rec = State_Recorder(db, backend, memories, elf)

    mems, regs = rec.get_state()
    backend.list_registers.assert_called_once()
    # All four registers got read
    assert set(regs.keys()) == {"r0", "r1", "sp", "pc"}
    # One memory region snapshotted
    assert 0x20000000 in mems


def test_add_function_accepts_int_addr(tmp_db_and_elf):
    """HalBackend.set_breakpoint requires an int, not a '*func' string."""
    from halucinator.util.profile_hals import State_Recorder

    db, elf = tmp_db_and_elf
    backend = _make_fake_backend()
    rec = State_Recorder(db, backend, [], elf)

    rec.add_function(0x08001234)
    backend.set_breakpoint.assert_called_once_with(0x08001234)


def test_get_state_skips_unknown_registers(tmp_db_and_elf):
    """If a register name isn't exposed by the backend, skip it rather
    than crashing."""
    from halucinator.util.profile_hals import State_Recorder

    db, elf = tmp_db_and_elf
    backend = _make_fake_backend(("r0", "cpsr", "mystery"))
    backend.read_register = mock.Mock(side_effect=lambda name: {
        "r0": 0x100, "cpsr": 0x60000010,
    }.get(name) if name != "mystery" else (_raise_value_error() if False
                                            else _raise_value_error()))

    def _raise():
        raise ValueError("unknown register: mystery")
    def _read(name):
        if name == "mystery":
            raise ValueError("unknown register: mystery")
        return {"r0": 0x100, "cpsr": 0x60000010}.get(name, 0)
    backend.read_register = mock.Mock(side_effect=_read)

    rec = State_Recorder(db, backend, [], elf)
    _, regs = rec.get_state()
    assert "r0" in regs
    assert "cpsr" in regs
    assert "mystery" not in regs


def _raise_value_error():
    raise ValueError("unknown register")
