"""Tests for halucinator.util.profile_hals module.

Note: The profile_hals module uses pickle for state serialization as part of
its existing design. This test file tests that existing functionality.
"""

import os
import sqlite3
from collections import deque
from unittest import mock

import pytest

from halucinator.util.profile_hals import State_Recorder


@pytest.fixture
def mock_gdb():
    m = mock.Mock()
    m.regs = mock.Mock()
    m.regs.lr = 0x08001000
    m.regs.pc = 0x08002000
    m.avatar = mock.Mock()
    m.avatar.arch = mock.Mock()
    m.avatar.arch.registers = {"r0": 0, "r1": 0, "sp": 0, "lr": 0, "pc": 0}
    m.read_memory.return_value = b"\x00" * 0x100
    m.read_register.return_value = 0
    return m


@pytest.fixture
def recorder(tmp_path, mock_gdb):
    """Create a State_Recorder with a temp DB and a dummy elf file."""
    db_path = str(tmp_path / "test.sqlite")
    elf_path = str(tmp_path / "test.elf")
    with open(elf_path, "wb") as f:
        f.write(b"\x7fELF" + b"\x00" * 100)

    memories = [(0x20000000, 0x100)]
    rec = State_Recorder(db_path, mock_gdb, memories, elf_path)
    return rec


class TestStateRecorder:
    def test_init_creates_db_tables(self, recorder, tmp_path):
        db_path = str(tmp_path / "test.sqlite")
        db = sqlite3.connect(db_path)
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        db.close()
        assert "applications" in tables
        assert "states" in tables

    def test_init_sets_app_id(self, recorder):
        assert recorder.app_id is not None
        assert recorder.app_id > 0

    def test_get_app_id_reuses_existing(self, recorder, tmp_path, mock_gdb):
        """Creating a second recorder with the same elf should reuse the app_id."""
        db_path = str(tmp_path / "test.sqlite")
        elf_path = str(tmp_path / "test.elf")
        memories = [(0x20000000, 0x100)]
        rec2 = State_Recorder(db_path, mock_gdb, memories, elf_path)
        assert rec2.app_id == recorder.app_id

    def test_add_function(self, recorder, mock_gdb):
        mock_gdb.set_breakpoint.return_value = 1
        recorder.add_function("HAL_Init")
        assert 1 in recorder.break_points
        assert recorder.break_points[1] == ("HAL_Init", True)

    def test_get_state(self, recorder, mock_gdb):
        mems, regs = recorder.get_state()
        assert 0x20000000 in mems
        assert len(regs) == len(mock_gdb.avatar.arch.registers)

    def test_save_state_entry(self, recorder, mock_gdb):
        record_id = recorder.save_state_to_db("test_func", is_entry=True)
        assert record_id is not None
        assert len(recorder.call_stack) == 1
        assert recorder.call_stack[0] == ("test_func", record_id)

    def test_save_state_exit(self, recorder, mock_gdb):
        # First save an entry
        entry_id = recorder.save_state_to_db("test_func", is_entry=True)
        # Then save an exit
        exit_id = recorder.save_state_to_db("test_func", is_entry=False)
        assert exit_id is not None
        assert len(recorder.call_stack) == 0

    def test_save_state_exit_mismatched_func_raises(self, recorder, mock_gdb):
        recorder.save_state_to_db("func_a", is_entry=True)
        with pytest.raises(ValueError, match="Call stack is off"):
            recorder.save_state_to_db("func_b", is_entry=False)

    def test_set_exit_bp_first_time(self, recorder, mock_gdb):
        mock_gdb.regs.lr = 0x08003000
        mock_gdb.set_breakpoint.return_value = 5
        recorder.set_exit_bp("test_func", 1)
        assert 0x08003000 in recorder.ret_addrs
        assert 5 in recorder.break_points

    def test_handle_bp_entry(self, recorder, mock_gdb):
        mock_gdb.set_breakpoint.return_value = 1
        recorder.add_function("HAL_Init")
        mock_gdb.regs.lr = 0x08005000
        mock_gdb.set_breakpoint.return_value = 2  # for exit bp
        recorder.handle_bp(1)
        assert len(recorder.call_stack) == 1

    def test_create_sql_tables_idempotent(self, recorder, tmp_path):
        db_path = str(tmp_path / "test.sqlite")
        db = sqlite3.connect(db_path)
        # Should not raise even if tables already exist
        recorder.create_sql_tables(db)
        db.close()


class TestHandleBpModule:
    def test_handle_bp_function(self):
        import halucinator.util.profile_hals as profile_mod
        from halucinator.util.profile_hals import handle_bp

        mock_avatar = mock.Mock()
        mock_message = mock.Mock()
        mock_message.breakpoint_number = "1"
        mock_message.origin = mock.Mock()

        mock_recorder = mock.Mock()
        # Recorder is a global set only in __main__; inject it manually
        profile_mod.Recorder = mock_recorder
        try:
            handle_bp(mock_avatar, mock_message)
            mock_recorder.handle_bp.assert_called_once_with(1)
            mock_message.origin.cont.assert_called_once()
        finally:
            del profile_mod.Recorder
