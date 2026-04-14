import struct
import types
from unittest import mock

import pytest

from halucinator.qemu_targets.hal_qemu import HALQemuTarget


@pytest.fixture
def qemu_mock():
    """Create a mock QEMU target with helper methods grafted on."""
    mock_model = mock.Mock()

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

    return mock_model
