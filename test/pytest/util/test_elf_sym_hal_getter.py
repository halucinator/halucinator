"""Tests for halucinator.util.elf_sym_hal_getter module."""

from unittest import mock

import pytest

from halucinator.util.elf_sym_hal_getter import (
    format_output,
    get_functions_and_addresses,
)


class TestFormatOutput:
    def test_basic_format(self):
        functions = {"main": 0x08000100, "init": 0x08000200}
        result = format_output(functions, base_addr=0x08000000, entry=0x08000100)
        assert result["architecture"] == "ARMEL"
        assert result["base_address"] == 0x08000000
        assert result["entry_point"] == 0x08000100
        assert "symbols" in result
        assert result["symbols"][0x08000100] == "main"
        assert result["symbols"][0x08000200] == "init"

    def test_default_base_and_entry(self):
        functions = {"func": 0x1000}
        result = format_output(functions)
        assert result["base_address"] == 0x00000000
        assert result["entry_point"] == 0

    def test_empty_functions(self):
        result = format_output({})
        assert result["symbols"] == {}


class TestGetFunctionsAndAddresses:
    @mock.patch("halucinator.util.elf_sym_hal_getter.load_binary")
    def test_returns_function_symbols(self, mock_load):
        sym1 = mock.Mock()
        sym1.is_function = True
        sym1.name = "main"
        sym1.rebased_addr = 0x08000101  # odd = thumb bit set

        sym2 = mock.Mock()
        sym2.is_function = False
        sym2.name = "data_var"
        sym2.rebased_addr = 0x20000000

        mock_loader = mock.Mock()
        mock_loader.symbols = [sym1, sym2]
        mock_load.return_value = mock_loader

        result = get_functions_and_addresses("test.elf")

        assert "main" in result
        assert result["main"] == 0x08000100  # Thumb bit cleared
        assert "data_var" not in result

    @mock.patch("halucinator.util.elf_sym_hal_getter.load_binary")
    def test_empty_binary(self, mock_load):
        mock_loader = mock.Mock()
        mock_loader.symbols = []
        mock_load.return_value = mock_loader

        result = get_functions_and_addresses("empty.elf")
        assert result == {}


class TestLoadBinary:
    @mock.patch("halucinator.util.elf_sym_hal_getter.cle")
    def test_load_binary_calls_cle(self, mock_cle, capsys):
        from halucinator.util.elf_sym_hal_getter import load_binary
        mock_loader_instance = mock.Mock()
        mock_cle.loader.Loader.return_value = mock_loader_instance

        result = load_binary("test_firmware.elf")

        mock_cle.loader.Loader.assert_called_once_with(
            "test_firmware.elf", auto_load_libs=False, use_system_libs=False
        )
        assert result is mock_loader_instance
        captured = capsys.readouterr()
        assert "Loading" in captured.out
        assert "test_firmware.elf" in captured.out


class TestBuildAddrToSymLookup:
    @mock.patch("halucinator.util.elf_sym_hal_getter.load_binary")
    def test_builds_sym_lut(self, mock_load):
        from halucinator.util.elf_sym_hal_getter import build_addr_to_sym_lookup

        sym_func = mock.Mock()
        sym_func.is_function = True
        sym_func.size = 4

        sym_data = mock.Mock()
        sym_data.is_function = False
        sym_data.size = 4

        mock_loader = mock.Mock()
        mock_loader.main_object.symbols_by_addr = {
            0x08000101: sym_func,  # odd addr = thumb bit set
            0x20000000: sym_data,
        }
        mock_load.return_value = mock_loader

        result = build_addr_to_sym_lookup("test.elf")
        # Function symbol should be in lut (thumb bit cleared: 0x08000100)
        assert 0x08000100 in result
        assert result[0x08000100] is sym_func
        # Data symbol should not be in lut
        assert 0x20000000 not in result

    @mock.patch("halucinator.util.elf_sym_hal_getter.load_binary")
    def test_empty_binary(self, mock_load):
        from halucinator.util.elf_sym_hal_getter import build_addr_to_sym_lookup

        mock_loader = mock.Mock()
        mock_loader.main_object.symbols_by_addr = {}
        mock_load.return_value = mock_loader

        result = build_addr_to_sym_lookup("empty.elf")
        assert result == {}


class TestMain:
    @mock.patch("halucinator.util.elf_sym_hal_getter.get_functions_and_addresses")
    @mock.patch("halucinator.util.elf_sym_hal_getter.yaml.safe_dump")
    @mock.patch("builtins.open", mock.mock_open())
    def test_main_default_output(self, mock_dump, mock_get_funcs):
        from halucinator.util.elf_sym_hal_getter import main
        mock_get_funcs.return_value = {"main": 0x8000}

        with mock.patch("sys.argv", ["prog", "-b", "firmware.elf"]):
            main()

        mock_get_funcs.assert_called_once_with("firmware.elf")
        mock_dump.assert_called_once()

    @mock.patch("halucinator.util.elf_sym_hal_getter.get_functions_and_addresses")
    @mock.patch("halucinator.util.elf_sym_hal_getter.yaml.safe_dump")
    @mock.patch("builtins.open", mock.mock_open())
    def test_main_custom_output(self, mock_dump, mock_get_funcs):
        from halucinator.util.elf_sym_hal_getter import main
        mock_get_funcs.return_value = {}

        with mock.patch("sys.argv", ["prog", "-b", "fw.elf", "-o", "out.yaml"]):
            main()

        mock_get_funcs.assert_called_once_with("fw.elf")
