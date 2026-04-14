import os
from unittest import mock

import pytest
from avatar2.peripherals.avatar_peripheral import AvatarPeripheral
from intervaltree import Interval

from halucinator.peripheral_models.generic import (
    GenericPeripheral,
    HaltPeripheral,
    hal_stats,
)


def init_helper(cls):
    name, address, size = ("peripheral_name", 0x10000, 0x100)
    peripheral = cls(name, address, size)
    assert isinstance(peripheral, AvatarPeripheral)
    assert peripheral.name == name
    assert peripheral.address == address
    assert peripheral.size == size
    assert peripheral.read_handler[0:size] == {
        Interval(0, size, peripheral.hw_read)
    }
    assert peripheral.write_handler[0:size] == {
        Interval(0, size, peripheral.hw_write)
    }


def write_helper(peripheral, cls):
    offset, size, value, pc = (0x1000, 4, 1, 0xBAADBEAD)
    rv = peripheral.hw_write(offset, size, value, pc)
    if cls == HaltPeripheral:
        assert rv is None
    else:
        assert rv is True
    addr = peripheral.address + offset
    assert hex(addr) in hal_stats.stats["MMIO_write_addresses"]
    assert hal_stats.stats["MMIO_write_addresses_length"] == 1
    assert hex(addr) in hal_stats.stats["MMIO_addresses"]
    assert hal_stats.stats["MMIO_addresses_length"] == 1
    if cls == HaltPeripheral:
        assert (hex(addr), hex(pc), "w") in hal_stats.stats["MMIO_addr_pc"]
    else:
        assert (
            f"{addr:#0{10}x},{pc:#0{10}x},w" in hal_stats.stats["MMIO_addr_pc"]
        )
    assert hal_stats.stats["MMIO_addr_pc_length"] == 1


def read_helper(peripheral, cls):
    offset, size, pc = (0x1000, 4, 0xBAADBEAD)
    rv = peripheral.hw_read(offset, size, pc)
    if cls == HaltPeripheral:
        assert rv is None
    else:
        assert rv == 0
    addr = peripheral.address + offset
    assert hex(addr) in hal_stats.stats["MMIO_read_addresses"]
    assert hal_stats.stats["MMIO_read_addresses_length"] == 1
    assert hex(addr) in hal_stats.stats["MMIO_addresses"]
    assert hal_stats.stats["MMIO_addresses_length"] == 1
    if cls == HaltPeripheral:
        assert (hex(addr), hex(pc), "r") in hal_stats.stats["MMIO_addr_pc"]
    else:
        assert (
            f"{addr:#0{10}x},{pc:#0{10}x},r" in hal_stats.stats["MMIO_addr_pc"]
        )
    assert hal_stats.stats["MMIO_addr_pc_length"] == 1


def setup_peripheral_helper(cls):
    hal_stats.stats["MMIO_read_addresses"] = set()
    hal_stats.stats["MMIO_write_addresses"] = set()
    hal_stats.stats["MMIO_addresses"] = set()
    hal_stats.stats["MMIO_addr_pc"] = set()
    stats_filename = "stats_file"
    hal_stats.set_filename(stats_filename)
    name, address, size = ("peripheral_name", 0x10000, 0x100)
    peripheral = cls(name, address, size)
    yield peripheral
    hal_stats.stats["MMIO_read_addresses"] = set()
    hal_stats.stats["MMIO_write_addresses"] = set()
    hal_stats.stats["MMIO_addresses"] = set()
    hal_stats.stats["MMIO_addr_pc"] = set()
    if os.path.exists(stats_filename):
        os.remove(stats_filename)


def test_generic_peripheral_init_creates_generic_peripheral_object():
    init_helper(GenericPeripheral)


@pytest.fixture()
def setup_generic_peripheral():
    yield from setup_peripheral_helper(GenericPeripheral)


def test_generic_peripheral_hw_write_updates_hal_stats(
    setup_generic_peripheral,
):
    generic_peripheral = setup_generic_peripheral
    write_helper(generic_peripheral, GenericPeripheral)


def test_generic_peripheral_hw_read_updates_hal_stats(
    setup_generic_peripheral,
):
    generic_peripheral = setup_generic_peripheral
    read_helper(generic_peripheral, 10)


def test_halt_peripheral_init_creates_halt_peripheral_object():
    init_helper(HaltPeripheral)


@pytest.fixture()
def setup_halt_peripheral():
    yield from setup_peripheral_helper(HaltPeripheral)


def test_halt_peripheral_hw_write_updates_hal_stats(setup_halt_peripheral):
    halt_peripheral = setup_halt_peripheral
    with mock.patch.object(HaltPeripheral, "infinite_loop", mock.Mock()):
        write_helper(halt_peripheral, HaltPeripheral)


def test_halt_peripheral_hw_read_updates_hal_stats(setup_halt_peripheral):
    halt_peripheral = setup_halt_peripheral
    with mock.patch.object(HaltPeripheral, "infinite_loop", mock.Mock()):
        read_helper(halt_peripheral, HaltPeripheral)
