"""Tests for halucinator.bp_handlers.vxworks.ios_dev"""
import os
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.ios_dev import IosDev


class TestIosDev:
    @pytest.fixture(autouse=True)
    def reset_class_state(self):
        """Reset class-level state between tests."""
        original_drivers = IosDev.drivers.copy()
        original_dir = IosDev.localDir
        yield
        IosDev.drivers = original_drivers
        IosDev.localDir = original_dir

    def test_get_driver_found(self):
        IosDev.drivers[0x100] = "/ata0a"
        assert IosDev.get_driver(0x100) == "/ata0a"

    def test_get_driver_not_found(self):
        assert IosDev.get_driver(0x999) is None

    def test_ios_dev_add(self, qemu, tmp_path):
        handler = IosDev()
        handler.localDir = str(tmp_path / "FS")
        os.makedirs(handler.localDir, exist_ok=True)

        def get_arg_side_effect(n):
            return [0x100, 0x200, 3][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/ata0a")

        # Mock the models
        IosDev.models = [mock.Mock()]

        result = handler.ios_dev_add(qemu, 0x1000)

        assert result == (False, None)
        assert IosDev.drivers[0x100] == "/ata0a"
        IosDev.models[0].attach_interface.assert_called_once_with("/ata0a")

    def test_ios_create(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [0x100, 0x200, 0x644][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="testfile.txt")

        result = handler.ios_create(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_delete(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [0x100, 0x200][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="deleteme.txt")

        result = handler.ios_delete(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_open(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [0x100, 0x200, 0x02, 0x1A4][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="myfile.txt")

        result = handler.ios_open(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_close(self, qemu):
        handler = IosDev()
        qemu.get_arg = mock.Mock(return_value=3)

        result = handler.ios_close(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_read(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [3, 0x5000, 256][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.ios_read(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_write(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [3, 0x5000, 256][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="Hello")

        result = handler.ios_write(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_ioctl(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [3, 0x01, 0x5000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.ios_ioctl(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_fd_new(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [0x100, 0x200][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="newfd")

        result = handler.ios_fd_new(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_fd_free(self, qemu):
        handler = IosDev()
        qemu.get_arg = mock.Mock(return_value=5)

        result = handler.ios_fd_free(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_fd_set(self, qemu):
        handler = IosDev()
        def get_arg_side_effect(n):
            return [5, 0x100, 0x200, 0x300][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="setname")

        result = handler.ios_fd_set(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_dev_find(self, qemu):
        handler = IosDev()
        qemu.get_arg = mock.Mock(return_value=0x2000)
        qemu.read_string = mock.Mock(return_value="/dev0")

        result = handler.ios_dev_find(qemu, 0x1000)
        assert result == (False, None)

    def test_ios_drv_install(self, qemu, tmp_path):
        handler = IosDev()
        # Mock get_arg to return values for DRV_INSTALL_NUM_ARGS args
        qemu.get_arg = mock.Mock(side_effect=lambda n: 0x100 + n)

        # Patch open to avoid real file writes
        with mock.patch("builtins.open", mock.mock_open()):
            result = handler.ios_drv_install(qemu, 0x1000)

        assert result == (False, None)

    def test_ios_error(self, qemu):
        handler = IosDev()
        result = handler.ios_error(qemu, 0x1000)
        assert result == (False, None)
