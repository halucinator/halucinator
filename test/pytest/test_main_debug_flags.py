"""Regression tests for the debug-server CLI flags.

Commit 2aa29b826d ("main.py + hal_config: per-backend orchestration +
--emulator routing") silently dropped ``--dap``/``--dap-bind`` and renamed
``--gdb-server`` to ``-d``/``--gdb_server_port``. Both flags are emitted by the
released halucinator-vscode extension, so the extension could no longer launch
HALucinator at all -- one flag was gone, the other unrecognised.

Nothing covered the CLI surface, so the breakage was invisible to CI. These
tests pin the flags and, more importantly, assert they are *plumbed through* to
``emulate_binary`` -- parsing alone would not have caught the original bug.
"""
import os
from unittest import mock

import pytest

from halucinator import main


EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "test", "STM32", "example",
)
CONFIG_ARGS = [
    "-c", os.path.join(EXAMPLE, "Uart_Hyperterminal_IT_O0_config.yaml"),
    "-c", os.path.join(EXAMPLE, "Uart_Hyperterminal_IT_O0_addrs.yaml"),
    "-c", os.path.join(EXAMPLE, "Uart_Hyperterminal_IT_O0_memory.yaml"),
]


def run_main(argv):
    """Invoke main.main() with argv, capturing the emulate_binary kwargs."""
    with mock.patch.object(main, "emulate_binary") as emulate:
        with mock.patch("sys.argv", ["halucinator"] + argv):
            main.main()
    assert emulate.call_count == 1, "emulate_binary was not reached"
    return emulate.call_args.kwargs


class Test_dap_flags:
    def test_dap_defaults_to_none(self):
        """Without --dap no DAP server is requested."""
        kwargs = run_main(CONFIG_ARGS)
        assert kwargs["dap_port"] is None

    def test_dap_bare_uses_default_port(self):
        """--dap with no value uses the well-known 34157 the extension expects."""
        kwargs = run_main(CONFIG_ARGS + ["--dap"])
        assert kwargs["dap_port"] == 34157

    def test_dap_explicit_port(self):
        kwargs = run_main(CONFIG_ARGS + ["--dap", "40000"])
        assert kwargs["dap_port"] == 40000

    def test_dap_bind_defaults_to_loopback(self):
        """The DAP server has no auth, so it must not bind the world by default."""
        kwargs = run_main(CONFIG_ARGS + ["--dap"])
        assert kwargs["dap_bind"] == "127.0.0.1"

    def test_dap_bind_override(self):
        kwargs = run_main(CONFIG_ARGS + ["--dap", "--dap-bind", "0.0.0.0"])
        assert kwargs["dap_bind"] == "0.0.0.0"


class Test_gdb_server_flags:
    def test_gdb_server_bare_uses_default_port(self):
        """--gdb-server with no value is what halucinator-vscode emits."""
        kwargs = run_main(CONFIG_ARGS + ["--gdb-server"])
        assert kwargs["gdb_server_port"] == 3333

    def test_gdb_server_explicit_port(self):
        kwargs = run_main(CONFIG_ARGS + ["--gdb-server", "1234"])
        assert kwargs["gdb_server_port"] == 1234

    def test_legacy_underscore_spelling_still_works(self):
        """-d/--gdb_server_port must keep working for existing scripts."""
        kwargs = run_main(CONFIG_ARGS + ["-d", "4444"])
        assert kwargs["gdb_server_port"] == 4444

    def test_gdb_server_wins_over_legacy_spelling(self):
        """Both given: the explicit --gdb-server spelling takes precedence."""
        kwargs = run_main(CONFIG_ARGS + ["-d", "4444", "--gdb-server", "3333"])
        assert kwargs["gdb_server_port"] == 3333

    def test_no_flag_means_no_server(self):
        kwargs = run_main(CONFIG_ARGS)
        assert kwargs["gdb_server_port"] is None


class Test_dap_requires_avatar2:
    """--dap needs avatar2's QemuTarget; the in-process backends can't host it.

    It must fail loudly rather than leaving a DAP client waiting on a port
    nothing ever binds.
    """

    @pytest.mark.parametrize("emulator", ["unicorn", "ghidra", "renode"])
    def test_dap_on_in_process_backend_exits(self, emulator):
        with pytest.raises(SystemExit) as exc:
            main.emulate_binary(
                mock.MagicMock(), dap_port=34157, emulator=emulator,
            )
        assert exc.value.code != 0

    def test_no_dap_on_in_process_backend_is_fine(self):
        """Sanity: the guard only fires for --dap, not for every backend call."""
        with mock.patch.object(main, "_emulate_with_backend") as backend:
            main.emulate_binary(mock.MagicMock(), emulator="unicorn")
        assert backend.call_count == 1
