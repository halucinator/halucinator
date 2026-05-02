import types
from unittest import mock

import pytest

from halucinator.qemu_targets.hal_qemu import HALQemuTarget
from halucinator.qemu_targets.arm_qemu import ARMQemuTarget


@pytest.fixture
def qemu_mock():
    mock_model = mock.Mock()

    # We want to test the read_memory mock calls at the level of
    # 'read_memory' and 'write_memory' to Avatar2 itself, because
    # that's the API boundary. Testing at that level instead of using
    # asserts like 'qemu.read_memory_word.assert_called_with(...)'
    # ensures that the interaction with the next layer down (Avatar)
    # is proper.
    #
    # The {read,write}_memory_{bytes,word} wrapper functions call
    # through to the underlying read_memory or write_memory function.
    # We snip the wrappers off of the HALQemuTarget class, where
    # they're implemented, and graft them onto this mock object.
    mock_model.read_memory_word = types.MethodType(
        HALQemuTarget.read_memory_word, mock_model
    )
    mock_model.read_memory_bytes = types.MethodType(
        HALQemuTarget.read_memory_bytes, mock_model
    )
    mock_model.write_memory_word = types.MethodType(
        HALQemuTarget.write_memory_word, mock_model
    )
    mock_model.write_memory_bytes = types.MethodType(
        HALQemuTarget.write_memory_bytes, mock_model
    )
    # Wire read_register to read from mock_model.regs so that
    # get_arg/set_args work the same way set_arguments() expects.
    def _read_register(reg_name):
        return getattr(mock_model.regs, reg_name)

    def _write_register(reg_name, value):
        setattr(mock_model.regs, reg_name, value)

    mock_model.read_register = _read_register
    mock_model.write_register = _write_register

    mock_model.get_arg = types.MethodType(
        ARMQemuTarget.get_arg, mock_model
    )
    mock_model.set_args = types.MethodType(
        ARMQemuTarget.set_args, mock_model
    )
    mock_model.read_string = types.MethodType(
        ARMQemuTarget.read_string, mock_model
    )

    return mock_model
