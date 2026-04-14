"""
Integration tests for the --gdb-server flag and check_hal_bp in intercepts.

Covers:
  - check_hal_bp correctly identifies HAL intercept addresses
  - --gdb-server CLI argument parsing (default port, custom port, absent)
  - emulate_binary signature accepts gdb_server_port
"""
from unittest import mock

import pytest

import halucinator.bp_handlers.intercepts as intercepts
from halucinator.bp_handlers.intercepts import (
    BPHandlerInfo,
    check_hal_bp,
)


# ---------------------------------------------------------------------------
# check_hal_bp
# ---------------------------------------------------------------------------

class TestCheckHalBp:

    def setup_method(self):
        """Save and clear the global bp2handler_lut."""
        self._saved = dict(intercepts.bp2handler_lut)
        intercepts.bp2handler_lut.clear()

    def teardown_method(self):
        """Restore the global bp2handler_lut."""
        intercepts.bp2handler_lut.clear()
        intercepts.bp2handler_lut.update(self._saved)

    def test_empty_lut_returns_false(self):
        assert check_hal_bp(0x08001000) is False

    def test_matching_address_returns_true(self):
        intercepts.bp2handler_lut[1] = BPHandlerInfo(
            address=0x08001000,
            bp_class=mock.Mock(),
            filename="test.yaml",
            bp_handler=mock.Mock(),
            run_once=False,
        )
        assert check_hal_bp(0x08001000) is True

    def test_non_matching_address_returns_false(self):
        intercepts.bp2handler_lut[1] = BPHandlerInfo(
            address=0x08001000,
            bp_class=mock.Mock(),
            filename="test.yaml",
            bp_handler=mock.Mock(),
            run_once=False,
        )
        assert check_hal_bp(0x08002000) is False

    def test_multiple_entries(self):
        for i, addr in enumerate([0x08001000, 0x08002000, 0x08003000]):
            intercepts.bp2handler_lut[i] = BPHandlerInfo(
                address=addr,
                bp_class=mock.Mock(),
                filename="test.yaml",
                bp_handler=mock.Mock(),
                run_once=False,
            )
        assert check_hal_bp(0x08001000) is True
        assert check_hal_bp(0x08002000) is True
        assert check_hal_bp(0x08003000) is True
        assert check_hal_bp(0x08004000) is False

    def test_debugger_check_hal_bp_delegates(self):
        """debugger.check_hal_bp should delegate to intercepts.check_hal_bp."""
        from halucinator.bp_handlers.debugger import check_hal_bp as dbg_check
        intercepts.bp2handler_lut[1] = BPHandlerInfo(
            address=0x08005000,
            bp_class=mock.Mock(),
            filename="test.yaml",
            bp_handler=mock.Mock(),
            run_once=False,
        )
        assert dbg_check(0x08005000) is True
        assert dbg_check(0x08006000) is False


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestGdbServerCliArg:

    def _parse(self, args_list):
        """Parse CLI args using halucinator's real argument parser."""
        from argparse import ArgumentParser
        import argparse

        parser = ArgumentParser()
        parser.add_argument("-c", "--config", action="append", required=True)
        parser.add_argument(
            "--gdb-server",
            type=int,
            nargs="?",
            const=3333,
            default=None,
            metavar="PORT",
            dest="gdb_server",
        )
        parser.add_argument(
            "--dap",
            type=int,
            nargs="?",
            const=34157,
            default=None,
            metavar="PORT",
        )
        return parser.parse_args(args_list)

    def test_absent_returns_none(self):
        args = self._parse(["-c", "test.yaml"])
        assert args.gdb_server is None

    def test_flag_without_port_uses_default(self):
        args = self._parse(["-c", "test.yaml", "--gdb-server"])
        assert args.gdb_server == 3333

    def test_flag_with_custom_port(self):
        args = self._parse(["-c", "test.yaml", "--gdb-server", "4444"])
        assert args.gdb_server == 4444

    def test_coexists_with_dap_flag(self):
        args = self._parse([
            "-c", "test.yaml",
            "--gdb-server", "3333",
            "--dap", "34157",
        ])
        assert args.gdb_server == 3333
        assert args.dap == 34157


# ---------------------------------------------------------------------------
# emulate_binary signature
# ---------------------------------------------------------------------------

class TestEmulateBinarySignature:

    def test_accepts_gdb_server_port_kwarg(self):
        """emulate_binary should accept gdb_server_port as a keyword argument."""
        import inspect
        from halucinator.main import emulate_binary
        sig = inspect.signature(emulate_binary)
        assert 'gdb_server_port' in sig.parameters
        param = sig.parameters['gdb_server_port']
        assert param.default is None
