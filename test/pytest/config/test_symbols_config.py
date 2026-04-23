"""
Tests for halucinator.config.symbols_config - HalSymbolConfig class
"""

import pytest

from halucinator.config.symbols_config import HalSymbolConfig


class TestHalSymbolConfigInit:
    def test_basic_construction(self):
        sc = HalSymbolConfig("cfg.yaml", "main", 0x1000)
        assert sc.config_file == "cfg.yaml"
        assert sc.name == "main"
        assert sc.addr == 0x1000
        assert sc.size == 0

    def test_with_size(self):
        sc = HalSymbolConfig("cfg.yaml", "func_a", 0x2000, size=128)
        assert sc.size == 128


class TestHalSymbolConfigIsValid:
    def test_always_valid(self):
        sc = HalSymbolConfig("cfg.yaml", "sym", 0)
        assert sc.is_valid()

    def test_valid_with_large_addr(self):
        sc = HalSymbolConfig("cfg.yaml", "sym", 0xFFFFFFFF, 0xFFFF)
        assert sc.is_valid()


class TestHalSymbolConfigRepr:
    def test_repr_format(self):
        sc = HalSymbolConfig("cfg.yaml", "main", 0x1000, 256)
        r = repr(sc)
        assert "cfg.yaml" in r
        assert "main" in r
        assert "0x1000" in r
        assert "4096" in r  # decimal of 0x1000
        assert "256" in r
