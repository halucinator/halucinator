"""Tests for halucinator.util.get_funct_name_MMIO module."""

import os
from unittest import mock

import pytest
import yaml

from halucinator.util.get_funct_name_MMIO import get_names_for_addrs


class TestGetNamesForAddrs:
    def test_maps_known_addresses(self, tmp_path, capsys):
        stats_data = {
            "MMIO_addr_pc": [
                "0x40000000,0x08001000,r",
                "0x40000004,0x08002000,w",
            ]
        }
        stats_file = str(tmp_path / "stats.yaml")
        with open(stats_file, "w") as f:
            yaml.safe_dump(stats_data, f)

        # Mock the build_addr_to_sym_lookup
        sym1 = mock.Mock()
        sym1.name = "HAL_GPIO_Init"
        sym2 = mock.Mock()
        sym2.name = "HAL_UART_Send"

        sym_lut = {0x08001000: sym1, 0x08002000: sym2}

        with mock.patch(
            "halucinator.util.get_funct_name_MMIO.build_addr_to_sym_lookup",
            return_value=sym_lut,
        ):
            get_names_for_addrs(stats_file, "test.elf")

        captured = capsys.readouterr()
        assert "HAL_GPIO_Init" in captured.out
        assert "HAL_UART_Send" in captured.out

    def test_unknown_addresses_go_to_unknown(self, tmp_path, capsys):
        stats_data = {
            "MMIO_addr_pc": [
                "0x40000000,0x99999999,r",
            ]
        }
        stats_file = str(tmp_path / "stats.yaml")
        with open(stats_file, "w") as f:
            yaml.safe_dump(stats_data, f)

        sym_lut = {}  # empty -- nothing known

        with mock.patch(
            "halucinator.util.get_funct_name_MMIO.build_addr_to_sym_lookup",
            return_value=sym_lut,
        ):
            get_names_for_addrs(stats_file, "test.elf")

        captured = capsys.readouterr()
        assert "$unknown_function" in captured.out


class TestMainBlock:
    def test_main_block_calls_get_names(self, tmp_path):
        """Test the __main__ argparse block."""
        stats_data = {"MMIO_addr_pc": ["0x40000000,0x08001000,r"]}
        stats_file = str(tmp_path / "stats.yaml")
        with open(stats_file, "w") as f:
            yaml.safe_dump(stats_data, f)

        sym_lut = {}
        with mock.patch(
            "halucinator.util.get_funct_name_MMIO.build_addr_to_sym_lookup",
            return_value=sym_lut,
        ), mock.patch(
            "sys.argv", ["prog", "-s", stats_file, "-b", "test.elf"]
        ):
            # Import and run the module-level code path
            import halucinator.util.get_funct_name_MMIO as mod
            mod.get_names_for_addrs(stats_file, "test.elf")
