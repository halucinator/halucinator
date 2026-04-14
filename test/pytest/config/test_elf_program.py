"""
Tests for halucinator.config.elf_program - ELFProgram class
"""

import os
from unittest import mock

import pytest

from halucinator.config.elf_program import ELFProgram
from halucinator.config.symbols_config import HalSymbolConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_hal_config():
    """Create a mock HalucinatorConfig."""
    hc = mock.Mock()
    hc.symbols = []
    hc.memories = {}
    return hc


def make_elf_config(name="test_prog", elf="test.elf", extra=None):
    """Build a minimal config dict for ELFProgram."""
    config = {"name": name, "elf": elf}
    if extra:
        config.update(extra)
    return config


# We need to prevent __init__ from running build/add_symbols automatically
# for most tests, so we patch those methods.

@pytest.fixture
def mock_elf_deps():
    """Patch ELFProgram methods that do I/O."""
    with mock.patch.object(ELFProgram, "run_build_cmd"), \
         mock.patch.object(ELFProgram, "add_symbols"):
        yield


# ---------------------------------------------------------------------------
# Tests: _parse_config
# ---------------------------------------------------------------------------

class TestParseConfig:
    def test_parses_minimal_config(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        assert ep.name == "test_prog"
        assert ep.build is None
        assert ep.execute_before is True
        assert ep.exit_function == "exit"
        assert ep.exit_to is None

    def test_parses_all_fields(self, mock_elf_deps):
        config = make_elf_config(extra={
            "build": {"cmd": "make", "dir": ".", "module_relative": None},
            "execute_before": False,
            "exit_function": "my_exit",
            "elf_module_relative": None,
            "intercepts": [{"handler": "foo", "symbol": "bar", "addr": 0x100}],
        })
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        assert ep.build is not None
        assert ep.execute_before is False
        assert ep.exit_function == "my_exit"
        assert len(ep._intercepts) == 1

    def test_absolute_elf_path(self, mock_elf_deps):
        config = make_elf_config(elf="/absolute/path/test.elf")
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        assert ep.elf_filename == "/absolute/path/test.elf"

    def test_relative_elf_path(self, mock_elf_deps):
        config = make_elf_config(elf="relative.elf")
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        assert ep.elf_filename == "/tmp/relative.elf"


# ---------------------------------------------------------------------------
# Tests: _validate_intercepts
# ---------------------------------------------------------------------------

class TestValidateIntercepts:
    def test_empty_intercepts_no_errors(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        # No intercepts, no errors
        assert ep._intercepts == []

    def test_intercepts_missing_handler_produces_error(self, mock_elf_deps):
        config = make_elf_config(extra={
            "intercepts": [{"symbol": "bar", "addr": 0x100}]
        })
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        # The intercept is missing 'handler', but validation just logs it


# ---------------------------------------------------------------------------
# Tests: get_fullpath
# ---------------------------------------------------------------------------

class TestGetFullpath:
    def test_absolute_path_returned_as_is(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        result = ep.get_fullpath("/abs/file.elf", "/tmp/cfg.yaml")
        assert result == "/abs/file.elf"

    def test_relative_path_joined_with_config_dir(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/opt/configs/cfg.yaml", config, hc)
        result = ep.get_fullpath("subdir/file.elf", "/opt/configs/cfg.yaml")
        assert result == "/opt/configs/subdir/file.elf"

    def test_relative_path_with_directory_config(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/opt/configs/", config, hc)
        result = ep.get_fullpath("file.elf", "/opt/configs/")
        assert result == "/opt/configs/file.elf"

    def test_module_relative_path(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        mock_module = mock.Mock()
        mock_module.__path__ = "/some/module/path"
        with mock.patch("importlib.import_module", return_value=mock_module):
            result = ep.get_fullpath("file.elf", "/tmp/cfg.yaml", module_str="some.module")
        assert "/some/module/path" in result
        assert "file.elf" in result


# ---------------------------------------------------------------------------
# Tests: get_sym_name
# ---------------------------------------------------------------------------

class TestGetSymName:
    def test_creates_unique_name(self, mock_elf_deps):
        config = make_elf_config(name="myprog")
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        assert ep.get_sym_name("main") == "$myprog$main"
        assert ep.get_sym_name("func_a") == "$myprog$func_a"


# ---------------------------------------------------------------------------
# Tests: get_function_addr
# ---------------------------------------------------------------------------

class TestGetFunctionAddr:
    def test_returns_addr_from_hal_config(self, mock_elf_deps):
        config = make_elf_config(name="prog")
        hc = make_mock_hal_config()
        hc.get_addr_for_symbol.return_value = 0x5000
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        result = ep.get_function_addr("func")
        hc.get_addr_for_symbol.assert_called_once_with("$prog$func")
        assert result == 0x5000

    def test_returns_none_when_not_found(self, mock_elf_deps):
        config = make_elf_config(name="prog")
        hc = make_mock_hal_config()
        hc.get_addr_for_symbol.return_value = None
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        result = ep.get_function_addr("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: run_build_cmd
# ---------------------------------------------------------------------------

class TestRunBuildCmd:
    def test_no_build_does_nothing(self):
        with mock.patch.object(ELFProgram, "add_symbols"):
            config = make_elf_config()
            hc = make_mock_hal_config()
            ep = ELFProgram("/tmp/cfg.yaml", config, hc)
            # build is None, run_build_cmd should be a no-op
            assert ep.build is None

    def test_build_runs_subprocess(self):
        with mock.patch.object(ELFProgram, "add_symbols"), \
             mock.patch("subprocess.run") as mock_run:
            config = make_elf_config(extra={
                "build": {"cmd": "make", "dir": "/tmp", "module_relative": None}
            })
            hc = make_mock_hal_config()
            ep = ELFProgram("/tmp/cfg.yaml", config, hc)
            mock_run.assert_called_once()

    def test_build_failure_exits(self):
        import subprocess
        with mock.patch.object(ELFProgram, "add_symbols"), \
             mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "make")), \
             pytest.raises(SystemExit):
            config = make_elf_config(extra={
                "build": {"cmd": "make", "dir": "/tmp", "module_relative": None}
            })
            hc = make_mock_hal_config()
            ep = ELFProgram("/tmp/cfg.yaml", config, hc)


# ---------------------------------------------------------------------------
# Tests: set_intercepts
# ---------------------------------------------------------------------------

class TestSetIntercepts:
    def test_sets_intercepts_on_target(self, mock_elf_deps):
        config = make_elf_config(name="prog", extra={
            "intercepts": [
                {"handler": "my_handler", "symbol": "target_func", "addr": 0x1000}
            ]
        })
        hc = make_mock_hal_config()
        hc.get_addr_for_symbol.side_effect = lambda name: {
            "$prog$my_handler": 0x5000,
        }.get(name)
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        qemu_target = mock.Mock()
        result = ep.set_intercepts(qemu_target)
        assert result is True
        qemu_target.write_branch.assert_called_once_with(0x1000, 0x5000)

    def test_handler_not_found_returns_false(self, mock_elf_deps):
        config = make_elf_config(name="prog", extra={
            "intercepts": [
                {"handler": "missing_handler", "symbol": "target", "addr": 0x1000}
            ]
        })
        hc = make_mock_hal_config()
        hc.get_addr_for_symbol.return_value = None
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        qemu_target = mock.Mock()
        result = ep.set_intercepts(qemu_target)
        assert result is False
        qemu_target.write_branch.assert_not_called()

    def test_intercept_with_symbol_lookup(self, mock_elf_deps):
        config = make_elf_config(name="prog", extra={
            "intercepts": [
                {"handler": "my_handler", "symbol": "target_func"}
            ]
        })
        hc = make_mock_hal_config()

        def get_addr(name):
            return {
                "$prog$my_handler": 0x5000,
                "target_func": 0x2000,  # symbol lookup for put_addr
            }.get(name)

        hc.get_addr_for_symbol.side_effect = get_addr
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        qemu_target = mock.Mock()
        result = ep.set_intercepts(qemu_target)
        assert result is True
        qemu_target.write_branch.assert_called_once_with(0x2000, 0x5000)

    def test_intercept_put_addr_not_found_returns_false(self, mock_elf_deps):
        """Test when handler found but put_addr (symbol) not found."""
        config = make_elf_config(name="prog", extra={
            "intercepts": [
                {"handler": "my_handler", "symbol": "missing_target"}
            ]
        })
        hc = make_mock_hal_config()

        def get_addr(name):
            return {
                "$prog$my_handler": 0x5000,
                # "missing_target" not present -> returns None
            }.get(name)

        hc.get_addr_for_symbol.side_effect = get_addr
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        qemu_target = mock.Mock()
        result = ep.set_intercepts(qemu_target)
        assert result is False
        qemu_target.write_branch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_calls_set_intercepts(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        qemu_target = mock.Mock()
        with mock.patch.object(ep, "set_intercepts") as mock_set:
            ep.initialize(qemu_target)
            mock_set.assert_called_once_with(qemu_target)

    def test_rewrites_exit_when_exit_to_set(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        hc.get_addr_for_symbol.return_value = 0x6000
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)
        ep.exit_to = 0x8000

        qemu_target = mock.Mock()
        with mock.patch.object(ep, "set_intercepts"):
            ep.initialize(qemu_target)
            qemu_target.write_branch.assert_called_once_with(0x6000, 0x8000)


# ---------------------------------------------------------------------------
# Tests: add_symbols (with real-ish ELF mock)
# ---------------------------------------------------------------------------

class TestAddSymbols:
    def test_adds_symbols_from_elf(self):
        """Test that add_symbols reads symbols from ELF and adds to config."""
        mock_sym = mock.Mock()
        mock_sym.name = "test_sym"
        mock_sym.__getitem__ = lambda self, key: {"st_value": 0x1234, "st_size": 64}[key]

        mock_symtab = mock.Mock()
        mock_symtab.num_symbols = 1
        mock_symtab.iter_symbols.return_value = [mock_sym]

        mock_elf = mock.Mock()
        mock_elf.get_section_by_name.return_value = mock_symtab

        with mock.patch.object(ELFProgram, "run_build_cmd"), \
             mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            config = make_elf_config(name="prog")
            hc = make_mock_hal_config()
            ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        assert len(hc.symbols) == 1
        assert hc.symbols[0].name == "$prog$test_sym"
        assert hc.symbols[0].addr == 0x1234
        assert hc.symbols[0].size == 64


# ---------------------------------------------------------------------------
# Tests: add_memories_configs
# ---------------------------------------------------------------------------

class TestAddMemoriesConfigs:
    def test_adds_memories_from_elf_segments(self, mock_elf_deps):
        config = make_elf_config(name="myprog")
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        mock_seg = mock.Mock()
        mock_seg.header.p_paddr = 0x08000000
        mock_seg.header.p_memsz = 0x1000
        mock_seg.header.p_align = 0x1000
        mock_seg.header.p_flags = 0x5  # r-x

        mock_elf = mock.Mock()
        mock_elf.num_segments.return_value = 1
        mock_elf.get_segment.return_value = mock_seg

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            ep.add_memories_configs()

        assert len(hc.memories) == 1

    def test_overlapping_segments_merged(self, mock_elf_deps):
        config = make_elf_config(name="myprog")
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        seg1 = mock.Mock()
        seg1.header.p_paddr = 0x08000000
        seg1.header.p_memsz = 0x2000
        seg1.header.p_align = 0x1000
        seg1.header.p_flags = 0x5

        seg2 = mock.Mock()
        seg2.header.p_paddr = 0x08001000  # overlaps with seg1
        seg2.header.p_memsz = 0x2000
        seg2.header.p_align = 0x1000
        seg2.header.p_flags = 0x6

        mock_elf = mock.Mock()
        mock_elf.num_segments.return_value = 2
        mock_elf.get_segment.side_effect = [seg1, seg2]

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            ep.add_memories_configs()

        # Should merge into 1 memory region
        assert len(hc.memories) == 1

    def test_conflict_with_existing_memory_exits(self, mock_elf_deps):
        from halucinator.config.memory_config import HalMemConfig
        config = make_elf_config(name="myprog")
        hc = make_mock_hal_config()
        # Pre-existing memory that overlaps
        existing_mem = mock.Mock()
        existing_mem.overlaps.return_value = True
        hc.memories = {"existing": existing_mem}
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        mock_seg = mock.Mock()
        mock_seg.header.p_paddr = 0x08000000
        mock_seg.header.p_memsz = 0x1000
        mock_seg.header.p_align = 0x1000
        mock_seg.header.p_flags = 0x5

        mock_elf = mock.Mock()
        mock_elf.num_segments.return_value = 1
        mock_elf.get_segment.return_value = mock_seg

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf), \
             pytest.raises(SystemExit):
            ep.add_memories_configs()


# ---------------------------------------------------------------------------
# Tests: get_entry_addr
# ---------------------------------------------------------------------------

class TestGetEntryAddr:
    def test_returns_entry_from_elf(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        mock_elf = mock.Mock()
        mock_elf.header = {"e_entry": 0x8000}

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            result = ep.get_entry_addr()
            assert result == 0x8000


# ---------------------------------------------------------------------------
# Tests: load_segments
# ---------------------------------------------------------------------------

class TestLoadSegments:
    def test_loads_pt_load_segments(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        mock_seg = mock.Mock()
        mock_seg.header.p_paddr = 0x8000000
        mock_seg.header.p_type = "PT_LOAD"
        mock_seg.data.return_value = b"\x00" * 16

        mock_elf = mock.Mock()
        mock_elf.num_segments.return_value = 1
        mock_elf.get_segment.return_value = mock_seg

        qemu_target = mock.Mock()

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            ep.load_segments(qemu_target)

        qemu_target.write_memory.assert_called_once_with(
            0x8000000, 1, b"\x00" * 16, raw=True
        )

    def test_skips_non_pt_load_segments(self, mock_elf_deps):
        config = make_elf_config()
        hc = make_mock_hal_config()
        ep = ELFProgram("/tmp/cfg.yaml", config, hc)

        mock_seg = mock.Mock()
        mock_seg.header.p_paddr = 0x8000000
        mock_seg.header.p_type = "PT_NOTE"

        mock_elf = mock.Mock()
        mock_elf.num_segments.return_value = 1
        mock_elf.get_segment.return_value = mock_seg

        qemu_target = mock.Mock()

        with mock.patch("builtins.open", mock.mock_open()), \
             mock.patch("halucinator.config.elf_program.ELFFile", return_value=mock_elf):
            ep.load_segments(qemu_target)

        qemu_target.write_memory.assert_not_called()
