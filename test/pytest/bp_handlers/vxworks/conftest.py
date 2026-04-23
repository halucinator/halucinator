"""Shared fixtures for VxWorks bp_handler tests."""
import types
from unittest import mock

import pytest

from halucinator.qemu_targets.hal_qemu import HALQemuTarget


@pytest.fixture
def qemu():
    """Create a mock qemu target with common attributes."""
    m = mock.Mock()

    # Graft real wrapper methods onto mock
    m.read_memory_word = types.MethodType(HALQemuTarget.read_memory_word, m)
    m.read_memory_bytes = types.MethodType(HALQemuTarget.read_memory_bytes, m)
    m.write_memory_word = types.MethodType(HALQemuTarget.write_memory_word, m)
    m.write_memory_bytes = types.MethodType(HALQemuTarget.write_memory_bytes, m)

    # Default register values
    m.regs = mock.Mock()
    m.regs.r0 = 0x1000
    m.regs.r1 = 0x2000
    m.regs.r2 = 0x3000
    m.regs.lr = 0x08001000
    m.regs.pc = 0x08002000

    # Default get_arg returns sequential addresses
    def get_arg_side_effect(n):
        return 0x1000 * (n + 1)
    m.get_arg = mock.Mock(side_effect=get_arg_side_effect)

    # Default read_string
    m.read_string = mock.Mock(return_value="test_string")

    # Default avatar config
    m.avatar = mock.Mock()
    m.avatar.config = mock.Mock()
    m.avatar.config.get_symbol_name = mock.Mock(return_value="some_symbol")
    m.avatar.config.get_addr_for_symbol = mock.Mock(return_value=0xDEAD0000)
    m.avatar.output_directory = "/tmp/halucinator_test"

    return m
