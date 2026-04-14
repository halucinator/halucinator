"""Tests for halucinator.bp_handlers.vxworks.yaf_fs"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.yaf_fs import YafFsLib
from halucinator.bp_handlers.vxworks.ios_dev import IosDev


class TestYafFsLib:
    @pytest.fixture(autouse=True)
    def reset_ios_drivers(self):
        original = IosDev.drivers.copy()
        yield
        IosDev.drivers = original

    def test_init_defaults(self):
        fs = YafFsLib()
        assert fs.dd_dirent_offset == 8

    def test_init_custom(self):
        model = mock.Mock()
        fs = YafFsLib(impl=model, dd_dirent_offset=16)
        assert fs.model is model
        assert fs.dd_dirent_offset == 16

    def test_attrib(self, qemu):
        fs = YafFsLib()
        def get_arg_side_effect(n):
            return [0x2000, 0x3000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(side_effect=["/myfile.txt", "+R"])

        result = fs.attrib(qemu, 0x5000)

        assert result == (True, 0)
        assert qemu.read_string.call_count == 2

    def test_delete(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.delete.return_value = 0

        IosDev.drivers[0x1000] = "/yaffs0"

        def get_arg_side_effect(n):
            return [0x1000, 0x2000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/myfile.txt")

        result = fs.delete(qemu, 0x5000)

        assert result == (True, 0)
        fs.model.delete.assert_called_once_with(0x1000, "/myfile.txt", "/yaffs0")

    def test_create(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.creat_or_open.return_value = (True, 5)

        IosDev.drivers[0x1000] = "/yaffs0"

        def get_arg_side_effect(n):
            return [0x1000, 0x2000, 0x200][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/newfile.txt")

        result = fs.create(qemu, 0x5000)

        assert result == (True, 5)
        fs.model.creat_or_open.assert_called_once_with(
            "/yaffs0/newfile.txt", 0x200 | 600, 0x8000
        )

    def test_open(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.creat_or_open.return_value = (True, 3)

        IosDev.drivers[0x1000] = "/yaffs0"

        def get_arg_side_effect(n):
            return [0x1000, 0x2000, 0x02, 0x1A4][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_string = mock.Mock(return_value="/test.txt")

        result = fs.open(qemu, 0x5000)

        assert result == (True, 3)
        fs.model.creat_or_open.assert_called_once_with(
            "/yaffs0/test.txt", 0x02, 0x1A4
        )

    def test_close_success(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.fd_table = {3: "/yaffs0/test.txt"}

        qemu.get_arg = mock.Mock(return_value=3)

        result = fs.close(qemu, 0x5000)

        assert result == (True, 0)
        fs.model.close.assert_called_once_with(3)

    def test_close_os_error(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.fd_table = {3: "/yaffs0/test.txt"}
        fs.model.close.side_effect = OSError("file error")

        qemu.get_arg = mock.Mock(return_value=3)

        result = fs.close(qemu, 0x5000)

        assert result == (True, 0xffffffff)

    def test_read(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.read.return_value = b"hello"

        def get_arg_side_effect(n):
            return [3, 0x5000, 256][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = fs.read(qemu, 0x5000)

        assert result == (True, 5)
        fs.model.read.assert_called_once_with(3, 256)
        # YafFs writes byte by byte
        assert qemu.write_memory.call_count == 5

    def test_read_empty(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.read.return_value = b""

        def get_arg_side_effect(n):
            return [3, 0x5000, 256][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = fs.read(qemu, 0x5000)

        assert result == (True, 0)
        qemu.write_memory.assert_not_called()

    def test_write(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()

        def get_arg_side_effect(n):
            return [3, 0x5000, 5][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(return_value=b"hello")

        result = fs.write(qemu, 0x5000)

        assert result == (True, 5)
        qemu.read_memory.assert_called_once_with(0x5000, 1, 5, raw=True)
        fs.model.write.assert_called_once_with(3, b"hello")

    def test_fio_move(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        qemu.read_string = mock.Mock(return_value="/new/path.txt")

        result = fs.fio_move(qemu, 0x5000, 3, 0x6000)

        assert result == (True, 0)
        fs.model.fio_move.assert_called_once_with(3, "/new/path.txt")

    def test_fio_time_set(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        qemu.read_memory = mock.Mock(side_effect=[
            (100).to_bytes(4, "little"),
            (200).to_bytes(4, "little"),
        ])

        result = fs.fio_time_set(qemu, 0x5000, 3, 0x6000)

        assert result == (True, 0)
        fs.model.fio_time_set.assert_called_once_with(3, 100, 200)

    def test_fio_rename(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        qemu.read_string = mock.Mock(return_value="newname.txt")

        result = fs.fio_rename(qemu, 0x5000, 3, 0x6000)

        assert result == (True, 0)
        fs.model.fio_rename.assert_called_once_with(3, "newname.txt")

    def test_fio_read(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.fio_read.return_value = 1024

        result = fs.fio_read(qemu, 0x5000, 3, 0x6000)

        assert result == (True, 0)
        qemu.write_memory.assert_called_once_with(0x6000, 4, 1024)

    def test_fio_where(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.fio_where.return_value = 512

        result = fs.fio_where(qemu, 0x5000, 3, 0x6000)

        assert result == (True, 512)

    def test_fio_seek(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()

        result = fs.fio_seek(qemu, 0x5000, 3, 100)

        assert result == (True, 0)
        fs.model.fio_seek.assert_called_once_with(3, 100)

    def test_fio_fstat_get(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.get_filename.return_value = "test.txt"
        fs.model.fio_fstat_get.return_value = {
            "st_dev": 1,
            "st_mode": 0o100644,
            "st_nlink": 1,
            "st_size": 1024,
            "st_atime": 1000,
            "st_mtime": 2000,
            "st_blksize": 512,
            "st_blocks": 2,
            "st_attrib": 0x20,
        }
        fs.model.st_dev = 4
        fs.model.st_mode = 4
        fs.model.st_nlink = 2
        fs.model.st_size = 4
        fs.model.st_atime_32 = 4
        fs.model.st_mtime_32 = 4
        fs.model.st_blksize = 4
        fs.model.st_blocks = 4

        result = fs.fio_fstat_get(qemu, 0x5000, 3, 0x7000)

        assert result == (True, 0)
        assert qemu.write_memory.call_count == 9

    def test_fio_read_dir_first_time(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.readdir = {}
        fs.model.fio_read_dir.return_value = [ord('t'), ord('e'), ord('s'), ord('t')]

        result = fs.fio_read_dir(qemu, 0x5000, 3, 0x8000)

        assert result == (True, 0)
        fs.model.fio_read_dir.assert_called_once_with(3, True)

    def test_fio_read_dir_subsequent(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.readdir = {3: True}
        fs.model.fio_read_dir.return_value = [ord('a'), ord('b')]

        result = fs.fio_read_dir(qemu, 0x5000, 3, 0x8000)

        assert result == (True, 0)
        fs.model.fio_read_dir.assert_called_once_with(3, False)

    def test_fio_read_dir_returns_none(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.readdir = {3: True}
        fs.model.fio_read_dir.return_value = None

        result = fs.fio_read_dir(qemu, 0x5000, 3, 0x8000)

        assert result == (True, None)

    def test_fio_attrib_set_noop(self, qemu):
        fs = YafFsLib()
        result = fs.fio_attrib_set(qemu, 0x5000, 3, 0x20)
        assert result is None

    def test_ioctl_with_function_handler(self, qemu):
        fs = YafFsLib()
        fs.model = mock.Mock()
        fs.model.fio_where.return_value = 100

        def get_arg_side_effect(n):
            return [3, 8, 0x6000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = fs.ioctl(qemu, 0x5000)

        assert result == (True, 100)

    def test_ioctl_with_string_handler(self, qemu):
        fs = YafFsLib()

        def get_arg_side_effect(n):
            return [3, 2, 0x6000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(return_value=[0, 0, 0, 0])

        result = fs.ioctl(qemu, 0x5000)

        assert result == (False, None)

    def test_ioctl_undefined_function(self, qemu):
        fs = YafFsLib()

        def get_arg_side_effect(n):
            return [3, 999, 0x6000][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        with mock.patch("builtins.input", return_value=""):
            result = fs.ioctl(qemu, 0x5000)

        assert result == (True, 0)
