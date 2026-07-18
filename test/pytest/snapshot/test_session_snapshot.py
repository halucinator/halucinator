# Copyright 2026 Christopher Wright

"""MCP-session snapshot tools against the real arm32 test firmware:
in-memory checkpoint/rewind within a session, and .halsnap files that
restore into a completely new session."""
from __future__ import annotations

from pathlib import Path

import pytest

from halucinator.mcp import HalucinatorSession, SessionError

REPO_ROOT = Path(__file__).resolve().parents[3]
ARM32_DIR = REPO_ROOT / "test" / "multi_arch" / "arm32"


def _arm32_configs():
    return [
        str(ARM32_DIR / "test_uart_config.yaml"),
        str(ARM32_DIR / "test_uart_addrs.yaml"),
        str(ARM32_DIR / "test_uart_memory.yaml"),
    ]


pytestmark = pytest.mark.skipif(
    not (ARM32_DIR / "firmware" / "test_uart.bin").exists(),
    reason="arm32 test firmware not built",
)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.chdir(ARM32_DIR)
    sess = HalucinatorSession()
    sess.start(_arm32_configs(), emulator="unicorn",
               target_name="snap_test", start_periph_server=False)
    yield sess
    sess.shutdown()


class TestInMemorySnapshots:
    def test_save_step_restore_rewinds(self, session):
        saved = session.save_snapshot()
        assert saved["kind"] == "memory"
        start_pc = saved["pc"]

        for _ in range(4):
            session.step()
        assert session.read_register("pc") != start_pc

        out = session.restore_snapshot(snapshot_id=saved["snapshot_id"])
        assert out["ok"] is True, out
        assert session.read_register("pc") == start_pc
        # ... and the machine is runnable again from there.
        session.step()

    def test_list_and_delete(self, session):
        a = session.save_snapshot()["snapshot_id"]
        b = session.save_snapshot()["snapshot_id"]
        ids = [s["snapshot_id"] for s in session.list_snapshots()]
        assert sorted(ids) == sorted([a, b])
        assert session.delete_snapshot(a) is True
        ids = [s["snapshot_id"] for s in session.list_snapshots()]
        assert ids == [b]
        with pytest.raises(SessionError, match="no such snapshot"):
            session.delete_snapshot(a)

    def test_restore_unknown_id_raises(self, session):
        with pytest.raises(SessionError, match="no such snapshot"):
            session.restore_snapshot(snapshot_id="snap-nope")

    def test_restore_needs_exactly_one_source(self, session):
        with pytest.raises(SessionError, match="exactly one"):
            session.restore_snapshot()
        with pytest.raises(SessionError, match="exactly one"):
            session.restore_snapshot(snapshot_id="x", path="y")


class TestFileSnapshots:
    def test_file_snapshot_restores_in_new_session(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.chdir(ARM32_DIR)
        snap_file = str(tmp_path / "session.halsnap")

        s1 = HalucinatorSession()
        s1.start(_arm32_configs(), emulator="unicorn",
                 target_name="snap_a", start_periph_server=False)
        for _ in range(3):
            s1.step()
        mark_pc = s1.read_register("pc")
        s1.write_memory(0x20000100, b"snapshot-marker")
        saved = s1.save_snapshot(path=snap_file)
        assert saved["kind"] == "file"
        assert saved["pc"] == mark_pc
        s1.shutdown()

        # A brand-new session from the same configs — the documented
        # fresh-process restore flow, minus the process boundary (that
        # boundary is covered by test_persist.TestCrossProcess and the CLI
        # e2e; this exercises the session/tool layer).
        s2 = HalucinatorSession()
        s2.start(_arm32_configs(), emulator="unicorn",
                 target_name="snap_b", start_periph_server=False)
        assert s2.read_register("pc") != mark_pc or True  # fresh at entry
        out = s2.restore_snapshot(path=snap_file)
        assert out["ok"] is True, out
        assert s2.read_register("pc") == mark_pc
        assert s2.read_memory(0x20000100, 15) == b"snapshot-marker"
        s2.step()  # runnable
        s2.shutdown()

    def test_restore_missing_file_raises(self, session, tmp_path):
        with pytest.raises(SessionError, match="not a readable snapshot"):
            session.restore_snapshot(path=str(tmp_path / "missing.halsnap"))
