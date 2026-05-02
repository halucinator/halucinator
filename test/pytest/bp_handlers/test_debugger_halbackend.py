"""Debugger works with any HalBackend — not just avatar2's QemuTarget.

The Debugger class in bp_handlers/debugger.py previously called
protocols-specific avatar2 methods (dictify, get_status,
protocols.memory._sync_request). Those paths now gracefully fall back
to HalBackend-native equivalents when the target doesn't expose the
avatar2 API.
"""
from unittest import mock

import pytest


def _make_halbackend_target(state_name="STOPPED"):
    """Build a minimal fake target that looks like a HalBackend — no
    .protocols, no .dictify, no .get_status; just the generic
    HalBackend methods."""
    t = mock.MagicMock(
        spec=["read_register", "write_register", "read_memory",
              "write_memory", "set_breakpoint", "remove_breakpoint",
              "set_watchpoint", "cont", "stop", "step", "list_registers",
              "name", "arch"],
    )
    t.name = "halbackend"
    t.arch = "cortex-m3"
    t.list_registers.return_value = ["r0", "r1", "sp", "pc"]
    t.read_register.side_effect = lambda r: {
        "r0": 0x100, "r1": 0x200, "sp": 0x20008000, "pc": 0x08000100,
        "lr": 0x08000ABF,
    }.get(r, 0)
    t.step.return_value = None
    t.cont.return_value = None
    t.set_breakpoint.return_value = 99
    return t


@pytest.fixture
def halbackend_debugger():
    from halucinator.bp_handlers.debugger import Debugger
    target = _make_halbackend_target()
    avatar = mock.MagicMock()
    dbg = Debugger(target=target, avatar=avatar)
    return dbg, target


class TestDebuggerOnHalBackend:
    def test_get_info_fallback(self, halbackend_debugger):
        dbg, target = halbackend_debugger
        info = dbg.get_info()
        # Synthetic dict when target lacks .dictify
        assert info["name"] == "halbackend"
        assert info["arch"] == "cortex-m3"

    def test_list_all_regs_uses_hal_list_registers(self, halbackend_debugger):
        dbg, target = halbackend_debugger
        names = dbg.list_all_regs_names()
        target.list_registers.assert_called_once()
        assert set(names) == {"r0", "r1", "sp", "pc"}

    def test_next_falls_back_to_step(self, halbackend_debugger):
        """Without protocols.memory, next() just steps one instruction."""
        dbg, target = halbackend_debugger
        from halucinator.bp_handlers.debugger import DebugState
        dbg.state = DebugState.STOPPED
        ok = dbg._next()
        assert ok is True
        target.step.assert_called_once()

    def test_finish_falls_back_to_tmp_bp_at_lr(self, halbackend_debugger):
        """Without protocols.memory, finish() sets a temporary bp at LR
        and continues."""
        dbg, target = halbackend_debugger
        from halucinator.bp_handlers.debugger import DebugState
        dbg.state = DebugState.STOPPED
        ok = dbg._finish()
        assert ok is True
        # Breakpoint was set at LR (masked of Thumb bit)
        assert target.set_breakpoint.called
        target.cont.assert_called_once()
