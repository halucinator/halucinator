# Copyright 2026 Christopher Wright

"""Tests for HalucinatorSession — the long-lived emulation handle that
backs the MCP server.

These tests exercise the session against the real arm32 multi_arch test
firmware (small, fast, ships in the repo) on the unicorn backend.
Backend fixtures + helpers in test/pytest/helpers/arm_helpers are
already used by test_live_feature_matrix; we drive the same primitives
here at the MCP-session layer instead."""
from __future__ import annotations

import os
import struct
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


def _arm32_firmware_present() -> bool:
    return (ARM32_DIR / "firmware" / "test_uart.bin").exists()


pytestmark = pytest.mark.skipif(
    not _arm32_firmware_present(),
    reason="arm32 test firmware not built",
)


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Spin up a HalucinatorSession against the arm32 firmware. The
    session changes cwd to the firmware dir because the YAML file
    paths are relative ('firmware/test_uart.bin')."""
    monkeypatch.chdir(ARM32_DIR)
    sess = HalucinatorSession()
    yield sess
    sess.shutdown()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestStartEmulation:
    def test_starts_and_reports_metadata(self, session):
        info = session.start(_arm32_configs(), emulator="unicorn",
                             target_name="mcp_test",
                             start_periph_server=False)
        assert info["emulator"] == "unicorn"
        assert info["arch"] == "cortex-m3"
        assert info["entry_addr"] == 0x08000113
        assert info["init_sp"] == 0x20080000
        assert info["intercepts"] >= 3  # uart_init/write/read
        # The PC was just initialised to entry_addr (Thumb bit kept).
        assert info["pc"] in (0x08000113, 0x08000112)

    def test_double_start_raises(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        with pytest.raises(SessionError, match="already active"):
            session.start(_arm32_configs(), emulator="unicorn",
                          target_name="mcp_test")

    def test_unsupported_backend_raises(self, session):
        with pytest.raises(SessionError, match="not supported"):
            session.start(_arm32_configs(), emulator="qemu")

    def test_missing_config_raises(self, session):
        with pytest.raises(SessionError, match="not found"):
            session.start(["/no/such/path.yaml"], emulator="unicorn")

    def test_empty_configs_raises(self, session):
        with pytest.raises(SessionError, match="at least one"):
            session.start([], emulator="unicorn")


# ---------------------------------------------------------------------------
# Pre-active guards
# ---------------------------------------------------------------------------

class TestInactiveGuards:
    @pytest.mark.parametrize("op", [
        lambda s: s.read_register("pc"),
        lambda s: s.write_register("pc", 0),
        lambda s: s.read_memory(0x100, 16),
        lambda s: s.write_memory(0x100, b"\x00"),
        lambda s: s.set_breakpoint(0x100),
        lambda s: s.list_intercepts(),
        lambda s: s.list_breakpoints(),
        lambda s: s.list_memory_regions(),
        lambda s: s.lookup_symbol("foo"),
    ])
    def test_inactive_session_raises(self, session, op):
        with pytest.raises(SessionError, match="No active emulation"):
            op(session)

    def test_status_inactive_returns_inactive_flag(self, session):
        assert session.status() == {"active": False}


# ---------------------------------------------------------------------------
# Memory + register access
# ---------------------------------------------------------------------------

class TestMemoryAndRegisters:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_read_register_pc_matches_entry(self):
        # Entry address from the YAML, low bit may be cleared by unicorn
        # when ARM enters Thumb state.
        pc = self.session.read_register("pc")
        assert pc & ~1 == 0x08000112

    def test_write_then_read_register(self):
        self.session.write_register("r0", 0xDEADBEEF)
        assert self.session.read_register("r0") == 0xDEADBEEF

    def test_read_registers_returns_full_set(self):
        regs = self.session.read_registers()
        assert "pc" in regs and "sp" in regs
        assert all(isinstance(v, int) for v in regs.values())

    def test_write_then_read_memory_in_ram(self):
        addr = 0x20001000
        payload = b"halucinator"
        ok = self.session.write_memory(addr, payload)
        assert ok is True
        roundtrip = self.session.read_memory(addr, len(payload))
        assert roundtrip == payload

    def test_read_memory_size_validation(self):
        with pytest.raises(SessionError, match="size must be"):
            self.session.read_memory(0x20001000, 0)
        with pytest.raises(SessionError, match="size must be"):
            self.session.read_memory(0x20001000, 0x10001)

    def test_write_memory_validation(self):
        with pytest.raises(SessionError, match="must not be empty"):
            self.session.write_memory(0x20001000, b"")
        with pytest.raises(SessionError, match="must be <= 65536"):
            self.session.write_memory(0x20001000, b"\x00" * 0x10001)

    def test_read_memory_returns_firmware_bytes_at_entry(self):
        # The firmware ELF/.bin has been loaded into flash @ 0x08000000;
        # reading 4 bytes at the entry yields a non-zero word (function
        # prologue).
        word = self.session.read_memory(0x08000110, 4)
        assert word != b"\x00\x00\x00\x00"


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

class TestBreakpoints:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_set_then_list(self):
        bp_id = self.session.set_breakpoint(0x08000200)
        bps = self.session.list_breakpoints()
        assert {"bp_id": bp_id, "addr": 0x08000200} in bps

    def test_remove_then_listed_empty(self):
        bp_id = self.session.set_breakpoint(0x08000200)
        ok = self.session.remove_breakpoint(bp_id)
        assert ok is True
        assert self.session.list_breakpoints() == []

    def test_intercepts_loaded_from_yaml(self):
        intercepts = self.session.list_intercepts()
        functions = {i["function"] for i in intercepts}
        assert {"uart_init", "uart_write", "uart_read"}.issubset(functions)


# ---------------------------------------------------------------------------
# Symbol lookup
# ---------------------------------------------------------------------------

class TestSymbolLookup:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_lookup_symbol_known(self):
        addr = self.session.lookup_symbol("uart_init")
        assert addr is not None and addr != 0

    def test_lookup_symbol_unknown_returns_none(self):
        assert self.session.lookup_symbol("definitely_not_a_real_symbol") is None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    def test_shutdown_inactive_session(self, session):
        out = session.shutdown()
        assert out["was_active"] is False

    def test_shutdown_then_start_again(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        session.shutdown()
        info = session.start(_arm32_configs(), emulator="unicorn",
                             target_name="mcp_test",
                             start_periph_server=False)
        assert info["arch"] == "cortex-m3"


# ---------------------------------------------------------------------------
# cont / step (shorter timeout — the firmware will hit the uart_init
# intercept within a handful of instructions of entry).
# ---------------------------------------------------------------------------

class TestExecution:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        # cont() needs the peripheral_server up because the firmware's
        # uart_write bp_handler publishes via UARTPublisher.write() over
        # zmq. step() doesn't, so this is the one place we pay the
        # zmq-bind cost.
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=True,
                      rx_port=15555, tx_port=15556)
        self.session = session

    def test_cont_blocking_hits_uart_init(self):
        # Multi_arch arm32 firmware: _start -> main -> uart_init ->
        # uart_write (×N) -> uart_read. uart_read's handler blocks
        # in UARTPublisher.read(block=True) until input arrives, so
        # we pre-load enough fake bytes for the firmware's first read
        # to satisfy. That lets the dispatch worker return cleanly
        # (no leaked thread between tests).
        from halucinator.peripheral_models.uart import UARTPublisher
        # Pre-load every plausible uart id so the firmware's blocking
        # uart_read is satisfied no matter which peripheral id it polls —
        # otherwise the handler parks in UARTPublisher.read(block=True) and
        # cont() times out. We can't read r0 to discover the id mid-run: the
        # running-guard contract rejects backend queries while a worker is in
        # flight, by design.
        for uart_id in range(8):
            UARTPublisher.rx_buffers[uart_id].extend(b"x" * 256)
        result = self.session.cont(blocking=True, timeout=5.0)
        assert result["state"] in ("hal_bp", "stopped", "debug_bp", "timeout")
        intercepts = self.session.list_intercepts()
        total_hits = sum(i["hit_count"] for i in intercepts)
        assert total_hits >= 1, "no intercepts fired — firmware didn't progress"
        # If it still timed out, the worker may be parked in a blocking
        # handler. Under the running-guard contract, stop() before touching
        # state; this settles the worker so pytest teardown sees clean
        # cross-test global state.
        if result["state"] == "timeout":
            self.session.stop()

    def test_step_advances_pc(self):
        pc_before = self.session.read_register("pc") & ~1
        self.session.step()
        pc_after = self.session.read_register("pc") & ~1
        assert pc_after != pc_before


# ---------------------------------------------------------------------------
# Analysis helpers — disassembly, watchpoints, strings, call args
# ---------------------------------------------------------------------------

class TestWatchpoints:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_set_list_remove(self):
        bp_id = self.session.set_watchpoint(0x20001000, write=True)
        assert {"bp_id": bp_id, "addr": 0x20001000} in \
            self.session.list_watchpoints()
        assert self.session.remove_watchpoint(bp_id) is True
        assert self.session.list_watchpoints() == []

    def test_requires_read_or_write(self):
        with pytest.raises(SessionError, match="reads, writes"):
            self.session.set_watchpoint(0x20001000, write=False, read=False)


class TestReadString:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_reads_nul_terminated(self):
        addr = 0x20001000
        self.session.write_memory(addr, b"halucinator\x00trailing")
        assert self.session.read_string(addr) == "halucinator"

    def test_max_len_validation(self):
        with pytest.raises(SessionError, match="max_len"):
            self.session.read_string(0x20001000, max_len=0)


class TestGetArgs:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_reads_register_args(self):
        for i in range(4):
            self.session.write_register(f"r{i}", 0x1000 + i)
        assert self.session.get_args(4) == [0x1000, 0x1001, 0x1002, 0x1003]

    def test_count_validation(self):
        with pytest.raises(SessionError, match="count must be"):
            self.session.get_args(17)


class TestDisassemble:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        pytest.importorskip("capstone")
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_disassembles_at_pc(self):
        insns = self.session.disassemble(count=4)
        assert 1 <= len(insns) <= 4
        first = insns[0]
        # PC starts at the entry (Thumb bit cleared for disassembly).
        assert first["addr"] == 0x08000112
        assert first["mnemonic"]
        assert len(first["bytes"]) == first["size"] * 2  # hex chars

    def test_explicit_addr(self):
        insns = self.session.disassemble(addr=0x08000112, count=1)
        assert insns and insns[0]["addr"] == 0x08000112

    def test_count_validation(self):
        with pytest.raises(SessionError, match="count must be"):
            self.session.disassemble(count=0)

    def test_x86_capstone_mapping(self, monkeypatch):
        # x86 is a supported unicorn arch; disassemble must have a capstone
        # mapping for it (regression: _capstone used to raise on x86). Decode
        # x86 bytes laid into RAM with the arch overridden to x86.
        monkeypatch.setattr(self.session.config.machine, "arch", "x86")
        # 0x90 nop ; 0xb8 imm32 (mov eax, 1) — exercises a 1-byte and a
        # 5-byte instruction, which the widened over-read must cover.
        self.session.write_memory(0x20001000, b"\x90\xb8\x01\x00\x00\x00")
        insns = self.session.disassemble(addr=0x20001000, count=2)
        assert insns[0]["mnemonic"] == "nop"
        assert insns[1]["mnemonic"] == "mov"
        assert insns[1]["size"] == 5


# ---------------------------------------------------------------------------
# Word read/write — endianness derived from the target arch, plus size param
# ---------------------------------------------------------------------------

class TestWordEndianness:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_little_endian_default(self):
        # arm32/cortex-m3 is little-endian.
        assert self.session.byteorder() == "little"
        self.session.write_word(0x20001000, 0x11223344)
        assert self.session.read_memory(0x20001000, 4) == b"\x44\x33\x22\x11"
        assert self.session.read_word(0x20001000) == 0x11223344

    def test_big_endian_arch(self, monkeypatch):
        # Pretend the same session is a big-endian target: the bytes laid
        # down in memory must be MSB-first and round-trip back.
        monkeypatch.setattr(self.session.config.machine, "arch", "mips")
        assert self.session.byteorder() == "big"
        self.session.write_word(0x20001000, 0x11223344)
        assert self.session.read_memory(0x20001000, 4) == b"\x11\x22\x33\x44"
        assert self.session.read_word(0x20001000) == 0x11223344

    def test_size_param(self):
        self.session.write_word(0x20001000, 0xABCD, size=2)
        assert self.session.read_memory(0x20001000, 2) == b"\xcd\xab"
        self.session.write_word(0x20001008, 0x1122334455667788, size=8)
        assert self.session.read_word(0x20001008, size=8) == 0x1122334455667788

    def test_invalid_size_rejected(self):
        with pytest.raises(SessionError, match="size must be"):
            self.session.read_word(0x20001000, size=3)
        with pytest.raises(SessionError, match="size must be"):
            self.session.write_word(0x20001000, 0, size=0)

    def test_oversized_value_masked_not_overflow(self):
        # A value wider than *size* wraps instead of raising OverflowError.
        self.session.write_word(0x20001000, 0x1_0000_00AB, size=4)
        assert self.session.read_word(0x20001000) == 0x0000_00AB


# ---------------------------------------------------------------------------
# Concurrency contract — backend queries are rejected while a non-blocking
# cont() worker is in flight; get_status stays available (and skips the PC).
# ---------------------------------------------------------------------------

class TestRunningGuard:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_query_rejected_while_running(self):
        import threading
        # Simulate an in-flight non-blocking cont by parking a live worker
        # thread — deterministic, no dependence on firmware timing.
        ev = threading.Event()
        worker = threading.Thread(target=ev.wait, daemon=True)
        worker.start()
        self.session._running = True
        self.session._worker = worker
        try:
            for op in (
                lambda: self.session.read_register("pc"),
                lambda: self.session.read_memory(0x20001000, 4),
                lambda: self.session.write_register("r0", 0),
                lambda: self.session.disassemble(count=1),
                lambda: self.session.set_breakpoint(0x08000200),
                lambda: self.session.read_word(0x20001000),
            ):
                with pytest.raises(SessionError, match="running"):
                    op()
            # get_status must stay callable and not touch the backend PC.
            st = self.session.status()
            assert st["running"] is True
            assert st["pc"] is None
        finally:
            ev.set()
            worker.join(timeout=1.0)
            self.session._running = False
            self.session._worker = None

    def test_stop_keeps_running_when_worker_survives_join(self):
        # A firmware that ignores emu_stop leaves the worker thread alive after
        # stop()'s join times out. stop() must NOT declare the engine idle
        # (that would let assert_idle pass and race the live engine), and
        # _snapshot_run must not read the backend PC.
        import threading
        ev = threading.Event()
        worker = threading.Thread(target=ev.wait, daemon=True)
        worker.start()
        self.session._running = True
        self.session._worker = worker
        try:
            snap = self.session.stop()       # join(2s) times out; worker alive
            assert snap["running"] is True    # still running, not cleared
            assert snap["pc"] is None         # backend not touched
            with pytest.raises(SessionError, match="running"):
                self.session.read_register("pc")
        finally:
            ev.set()
            worker.join(timeout=1.0)
            self.session._running = False
            self.session._worker = None


# ---------------------------------------------------------------------------
# step() guards against an exited firmware (mirrors cont()'s guard)
# ---------------------------------------------------------------------------

class TestStepExitedGuard:
    @pytest.fixture(autouse=True)
    def _activate(self, session):
        session.start(_arm32_configs(), emulator="unicorn",
                      target_name="mcp_test", start_periph_server=False)
        self.session = session

    def test_step_after_exit_raises(self):
        self.session._exited = True
        try:
            with pytest.raises(SessionError, match="exited"):
                self.session.step()
        finally:
            self.session._exited = False
