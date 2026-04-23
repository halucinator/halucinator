"""
Tests for halucinator.config.memory_config - HalMemConfig class
"""

import os

import pytest

from halucinator.config.memory_config import HalMemConfig


class TestHalMemConfigInit:
    def test_basic_construction(self):
        mc = HalMemConfig("ram", "/tmp/test.yaml", 0x20000000, 0x10000)
        assert mc.name == "ram"
        assert mc.config_file == "/tmp/test.yaml"
        assert mc.base_addr == 0x20000000
        assert mc.size == 0x10000
        assert mc.permissions == "rwx"
        assert mc.file is None
        assert mc.emulate is None
        assert mc.qemu_name is None
        assert mc.properties is None
        assert mc.irq_config is None
        assert mc.emulate_required is False

    def test_with_all_params(self):
        mc = HalMemConfig(
            "periph", "/tmp/test.yaml", 0x40000000, 0x1000,
            permissions="rw-",
            file="firmware.bin",
            emulate="GenericPeripheral",
            qemu_name="uart0",
            properties={"baud": 115200},
            irq={"num": 5},
        )
        assert mc.permissions == "rw-"
        assert mc.emulate == "GenericPeripheral"
        assert mc.qemu_name == "uart0"
        assert mc.properties == {"baud": 115200}
        assert mc.irq_config == {"num": 5}

    def test_file_path_made_relative_to_config(self):
        mc = HalMemConfig("ram", "/opt/configs/test.yaml", 0, 0x1000, file="fw.bin")
        assert mc.file == "/opt/configs/fw.bin"

    def test_absolute_file_path_not_modified(self):
        mc = HalMemConfig("ram", "/opt/configs/test.yaml", 0, 0x1000, file="/abs/fw.bin")
        assert mc.file == "/abs/fw.bin"

    def test_no_file_no_full_path_call(self):
        mc = HalMemConfig("ram", "/tmp/test.yaml", 0, 0x1000)
        assert mc.file is None


class TestHalMemConfigOverlaps:
    def test_no_overlap_below(self):
        a = HalMemConfig("a", "", 0x2000, 0x1000)
        b = HalMemConfig("b", "", 0x0000, 0x1000)
        assert not a.overlaps(b)

    def test_no_overlap_above(self):
        a = HalMemConfig("a", "", 0x2000, 0x1000)
        b = HalMemConfig("b", "", 0x4000, 0x1000)
        assert not a.overlaps(b)

    def test_overlap_partial_below(self):
        a = HalMemConfig("a", "", 0x2000, 0x1000)
        b = HalMemConfig("b", "", 0x1800, 0x1000)
        assert a.overlaps(b)

    def test_overlap_partial_above(self):
        a = HalMemConfig("a", "", 0x2000, 0x1000)
        b = HalMemConfig("b", "", 0x2800, 0x1000)
        assert a.overlaps(b)

    def test_overlap_contained(self):
        a = HalMemConfig("a", "", 0x2000, 0x2000)
        b = HalMemConfig("b", "", 0x2500, 0x100)
        assert a.overlaps(b)

    def test_overlap_surrounding(self):
        a = HalMemConfig("a", "", 0x2500, 0x100)
        b = HalMemConfig("b", "", 0x2000, 0x2000)
        assert a.overlaps(b)

    def test_adjacent_no_overlap(self):
        a = HalMemConfig("a", "", 0x2000, 0x1000)
        b = HalMemConfig("b", "", 0x3000, 0x1000)
        assert not a.overlaps(b)


class TestHalMemConfigIsValid:
    def test_valid_aligned_size(self):
        mc = HalMemConfig("ram", "", 0, 4096)
        assert mc.is_valid()

    def test_valid_zero_size(self):
        mc = HalMemConfig("ram", "", 0, 0)
        assert mc.is_valid()

    def test_invalid_unaligned_size(self):
        mc = HalMemConfig("ram", "", 0, 1234)
        assert not mc.is_valid()

    def test_emulate_required_but_missing(self):
        mc = HalMemConfig("ram", "", 0, 4096)
        mc.emulate_required = True
        assert not mc.is_valid()

    def test_emulate_required_and_present(self):
        mc = HalMemConfig("ram", "", 0, 4096, emulate="GenericPeripheral")
        mc.emulate_required = True
        assert mc.is_valid()


class TestHalMemConfigRepr:
    def test_repr_format(self):
        mc = HalMemConfig("flash", "cfg.yaml", 0x08000000, 0x200000)
        r = repr(mc)
        assert "flash" in r
        assert "0x8000000" in r
        assert "0x200000" in r
        assert "cfg.yaml" in r
