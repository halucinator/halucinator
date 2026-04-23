"""
Tests for halucinator.bp_handlers.zephyr.zephyr_fs
"""

import os
import struct
from stat import S_IFDIR, S_IFREG
from unittest import mock

import pytest

from halucinator.bp_handlers.zephyr.zephyr_fs import ZephyrFS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeStatResult:
    """Minimal stat_result-like object."""
    def __init__(self, mode, size=0):
        self.st_mode = mode
        self.st_size = size


class FakeStatvfsResult:
    """Minimal statvfs_result-like object."""
    def __init__(self, f_bsize=4096, f_frsize=4096, f_blocks=1024, f_bfree=512):
        self.f_bsize = f_bsize
        self.f_frsize = f_frsize
        self.f_blocks = f_blocks
        self.f_bfree = f_bfree


def make_qemu_mock():
    """Build a mock QEMU target."""
    qemu = mock.Mock()
    # By default get_arg returns 0; tests override per call
    qemu.get_arg = mock.Mock(side_effect=lambda idx: 0)
    return qemu


def make_read_string_bytes(s):
    """Return bytes that read_string will reconstruct as *s*.

    read_string reads one byte at a time until it hits \\x00.
    """
    return list(s.encode("utf-8")) + [0]


def setup_read_string(qemu, addr, text):
    """Configure qemu.read_memory so that read_string(qemu, addr) returns *text*."""
    char_bytes = make_read_string_bytes(text)

    def read_mem_side_effect(a, sz, cnt, raw=False):
        offset = a - addr
        if 0 <= offset < len(char_bytes):
            return bytes([char_bytes[offset]])
        return bytes([0])

    qemu.read_memory = mock.Mock(side_effect=read_mem_side_effect)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fs_handler():
    """Create a ZephyrFS with a mocked HostFSModel."""
    mock_impl = mock.Mock()
    # Make __init__ return None (proper for __init__)
    mock_impl_instance = mock.Mock()
    mock_impl.return_value = mock_impl_instance
    handler = ZephyrFS(impl=mock_impl)
    return handler


@pytest.fixture
def qemu():
    return make_qemu_mock()


# ---------------------------------------------------------------------------
# Tests: read_string
# ---------------------------------------------------------------------------

class TestReadString:
    def test_reads_null_terminated_string(self, fs_handler):
        qemu = make_qemu_mock()
        text = "hello"
        addr = 0x1000
        setup_read_string(qemu, addr, text)
        result = fs_handler.read_string(qemu, addr)
        assert result == text

    def test_reads_empty_string(self, fs_handler):
        qemu = make_qemu_mock()
        addr = 0x2000
        setup_read_string(qemu, addr, "")
        result = fs_handler.read_string(qemu, addr)
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: flash_area_stub
# ---------------------------------------------------------------------------

class TestFlashAreaStub:
    def test_returns_true_and_zero(self, fs_handler, qemu):
        ret_intercept, ret_val = fs_handler.flash_area_stub(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0


# ---------------------------------------------------------------------------
# Tests: fs_mount
# ---------------------------------------------------------------------------

class TestFsMount:
    def test_calls_model_mount(self, fs_handler, qemu):
        mount_struct_addr = 0x3000
        mount_path_addr = 0x4000
        mount_path = "/mnt/storage"
        fs_type = 5

        qemu.get_arg = mock.Mock(side_effect=lambda idx: mount_struct_addr)

        # Build the 32-byte struct: 8 LE uint32s
        # data_unpack[3] = mount_path_addr, data_unpack[5] = fs_type
        fields = [0, 0, 0, mount_path_addr, 0, fs_type, 0, 0]
        struct_data = struct.pack("<LLLLLLLL", *fields)

        # Track calls: first call is read_memory for the struct, subsequent for read_string
        string_bytes = make_read_string_bytes(mount_path)

        def read_mem(addr, sz, cnt, raw=False):
            if addr == mount_struct_addr and cnt == 0x20:
                return struct_data
            # read_string calls
            offset = addr - mount_path_addr
            if 0 <= offset < len(string_bytes):
                return bytes([string_bytes[offset]])
            return bytes([0])

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.mount.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_mount(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.mount.assert_called_once_with(mount_path, fs_type)


# ---------------------------------------------------------------------------
# Tests: fs_statvfs
# ---------------------------------------------------------------------------

class TestFsStatvfs:
    def test_writes_statvfs_data(self, fs_handler, qemu):
        path_addr = 0x5000
        out_addr = 0x6000
        stat_path = "/mnt"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [path_addr, out_addr][idx])

        setup_read_string(qemu, path_addr, stat_path)
        fake_statvfs = FakeStatvfsResult(4096, 4096, 1024, 512)
        fs_handler.model.statvfs.return_value = fake_statvfs

        ret_intercept, ret_val = fs_handler.fs_statvfs(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.statvfs.assert_called_once_with(stat_path)
        # Check that write_memory was called with the packed struct
        qemu.write_memory.assert_called()


# ---------------------------------------------------------------------------
# Tests: fs_stat
# ---------------------------------------------------------------------------

class TestFsStat:
    def test_stat_file_returns_info(self, fs_handler, qemu):
        path_addr = 0x5000
        out_addr = 0x6000
        stat_path = "/mnt/file.txt"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [path_addr, out_addr][idx])
        setup_read_string(qemu, path_addr, stat_path)

        fake_stat = FakeStatResult(S_IFREG | 0o644, size=1234)
        fs_handler.model.stat.return_value = (0, fake_stat)

        ret_intercept, ret_val = fs_handler.fs_stat(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.stat.assert_called_once_with(stat_path)
        qemu.write_memory.assert_called()

    def test_stat_directory(self, fs_handler, qemu):
        path_addr = 0x5000
        out_addr = 0x6000
        stat_path = "/mnt/dir"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [path_addr, out_addr][idx])
        setup_read_string(qemu, path_addr, stat_path)

        fake_stat = FakeStatResult(S_IFDIR | 0o755, size=0)
        fs_handler.model.stat.return_value = (0, fake_stat)

        ret_intercept, ret_val = fs_handler.fs_stat(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0

    def test_stat_not_found(self, fs_handler, qemu):
        path_addr = 0x5000
        out_addr = 0x6000
        stat_path = "/mnt/nofile"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [path_addr, out_addr][idx])
        setup_read_string(qemu, path_addr, stat_path)

        fs_handler.model.stat.return_value = (-2, None)

        ret_intercept, ret_val = fs_handler.fs_stat(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == -2
        # write_memory should NOT be called when info is None
        qemu.write_memory.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: fs_unlink
# ---------------------------------------------------------------------------

class TestFsUnlink:
    def test_calls_model_unlink(self, fs_handler, qemu):
        path_addr = 0x5000
        f_path = "/mnt/deleteme.txt"

        qemu.get_arg = mock.Mock(return_value=path_addr)
        setup_read_string(qemu, path_addr, f_path)
        fs_handler.model.unlink.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_unlink(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.unlink.assert_called_once_with(f_path)


# ---------------------------------------------------------------------------
# Tests: fs_open
# ---------------------------------------------------------------------------

class TestFsOpen:
    def test_opens_file_and_writes_struct(self, fs_handler, qemu):
        p_file = 0x7000
        p_path = 0x8000
        f_path = "/mnt/test.txt"
        open_flags = 0x03
        fs_handler.mount = 0x9000

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, p_path, open_flags][idx])

        # Build initial file struct (9 bytes: LLB)
        initial_struct = struct.pack("<LLB", 0, 0, 0)
        string_bytes = make_read_string_bytes(f_path)

        def read_mem(addr, sz, cnt, raw=False):
            if addr == p_file and cnt == 0x9:
                return initial_struct
            offset = addr - p_path
            if 0 <= offset < len(string_bytes):
                return bytes([string_bytes[offset]])
            return bytes([0])

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.open.return_value = (0, 42)

        ret_intercept, ret_val = fs_handler.fs_open(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.open.assert_called_once_with(f_path, open_flags)
        qemu.write_memory.assert_called()


# ---------------------------------------------------------------------------
# Tests: fs_read
# ---------------------------------------------------------------------------

class TestFsRead:
    def test_reads_data_and_writes_to_memory(self, fs_handler, qemu):
        p_file = 0x7000
        p_dst = 0xA000
        size = 10
        p_fs = 42

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, p_dst, size][idx])

        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)

        data = b"helloworld"
        fs_handler.model.read.return_value = (len(data), data)

        ret_intercept, ret_val = fs_handler.fs_read(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == len(data)
        fs_handler.model.read.assert_called_once_with(p_fs, size)
        # write_memory called to write data to p_dst
        qemu.write_memory.assert_called_once_with(p_dst, 1, data, len(data), raw=True)

    def test_read_empty_does_not_write(self, fs_handler, qemu):
        p_file = 0x7000
        p_dst = 0xA000
        size = 10
        p_fs = 42

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, p_dst, size][idx])

        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)

        fs_handler.model.read.return_value = (0, b"")

        ret_intercept, ret_val = fs_handler.fs_read(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        # write_memory should NOT be called for empty data
        qemu.write_memory.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: fs_write
# ---------------------------------------------------------------------------

class TestFsWrite:
    def test_writes_data_to_model(self, fs_handler, qemu):
        p_file = 0x7000
        write_data_addr = 0xB000
        amount = 5
        p_fs = 42
        data = b"hello"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, write_data_addr, amount][idx])

        file_struct = struct.pack("<LLB", p_fs, 0, 0)

        call_count = [0]
        def read_mem(addr, sz, cnt, raw=False):
            call_count[0] += 1
            if addr == p_file and cnt == 0x9:
                return file_struct
            if addr == write_data_addr:
                return data
            return b""

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.write.return_value = 5

        ret_intercept, ret_val = fs_handler.fs_write(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 5
        fs_handler.model.write.assert_called_once_with(p_fs, data)


# ---------------------------------------------------------------------------
# Tests: fs_seek
# ---------------------------------------------------------------------------

class TestFsSeek:
    def test_calls_model_seek(self, fs_handler, qemu):
        p_file = 0x7000
        f_offset = 100
        seek_flag = 0  # SEEK_SET
        p_fs = 42

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, f_offset, seek_flag][idx])

        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)
        fs_handler.model.seek.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_seek(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.seek.assert_called_once_with(p_fs, f_offset, seek_flag)


# ---------------------------------------------------------------------------
# Tests: fs_close
# ---------------------------------------------------------------------------

class TestFsClose:
    def test_closes_file_and_zeroes_struct(self, fs_handler, qemu):
        p_file = 0x7000
        p_fs = 42
        flags = 0x03

        qemu.get_arg = mock.Mock(return_value=p_file)

        file_struct = struct.pack("<LLB", p_fs, 0x9000, flags)
        qemu.read_memory = mock.Mock(return_value=file_struct)
        fs_handler.model.close.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_close(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.close.assert_called_once_with(p_fs)
        # write_memory should zero out p_fs and p_mount
        expected_data = struct.pack("<LLB", 0, 0, flags)
        qemu.write_memory.assert_called_once_with(p_file, 1, expected_data, len(expected_data), raw=True)


# ---------------------------------------------------------------------------
# Tests: fs_unmount
# ---------------------------------------------------------------------------

class TestFsUnmount:
    def test_calls_model_unmount(self, fs_handler, qemu):
        struct_addr = 0x3000
        mount_path_addr = 0x4000
        mount_path = "/mnt/storage"
        fs_type = 5

        qemu.get_arg = mock.Mock(return_value=struct_addr)

        fields = [0, 0, 0, mount_path_addr, 0, fs_type, 0, 0]
        struct_data = struct.pack("<LLLLLLLL", *fields)

        string_bytes = make_read_string_bytes(mount_path)

        def read_mem(addr, sz, cnt, raw=False):
            if addr == struct_addr and cnt == 0x20:
                return struct_data
            offset = addr - mount_path_addr
            if 0 <= offset < len(string_bytes):
                return bytes([string_bytes[offset]])
            return bytes([0])

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.unmount.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_unmount(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.unmount.assert_called_once_with(mount_path, fs_type)


# ---------------------------------------------------------------------------
# Tests: fs_tell
# ---------------------------------------------------------------------------

class TestFsTell:
    def test_returns_file_position(self, fs_handler, qemu):
        p_file = 0x7000
        p_fs = 42

        qemu.get_arg = mock.Mock(return_value=p_file)
        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)
        fs_handler.model.tell.return_value = 256

        ret_intercept, ret_val = fs_handler.fs_tell(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 256
        fs_handler.model.tell.assert_called_once_with(p_fs)


# ---------------------------------------------------------------------------
# Tests: fs_sync
# ---------------------------------------------------------------------------

class TestFsSync:
    def test_calls_model_sync(self, fs_handler, qemu):
        p_file = 0x7000
        p_fs = 42

        qemu.get_arg = mock.Mock(return_value=p_file)
        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)
        fs_handler.model.sync.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_sync(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.sync.assert_called_once_with(p_fs)


# ---------------------------------------------------------------------------
# Tests: fs_closedir
# ---------------------------------------------------------------------------

class TestFsClosedir:
    def test_calls_model_closedir(self, fs_handler, qemu):
        p_file = 0x7000
        p_fs = 42

        qemu.get_arg = mock.Mock(return_value=p_file)
        dir_struct = struct.pack("<LL", p_fs, 0)
        qemu.read_memory = mock.Mock(return_value=dir_struct)
        fs_handler.model.closedir.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_closedir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.closedir.assert_called_once_with(p_fs)


# ---------------------------------------------------------------------------
# Tests: fs_mkdir
# ---------------------------------------------------------------------------

class TestFsMkdir:
    def test_calls_model_mkdir(self, fs_handler, qemu):
        path_addr = 0x5000
        d_path = "/mnt/newdir"

        qemu.get_arg = mock.Mock(return_value=path_addr)
        setup_read_string(qemu, path_addr, d_path)
        fs_handler.model.mkdir.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_mkdir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.mkdir.assert_called_once_with(d_path)


# ---------------------------------------------------------------------------
# Tests: fs_opendir
# ---------------------------------------------------------------------------

class TestFsOpendir:
    def test_opens_directory(self, fs_handler, qemu):
        p_dir = 0x7000
        p_path = 0x8000
        d_path = "/mnt/mydir"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_dir, p_path][idx])

        dir_struct = struct.pack("<LL", 0, 0)
        string_bytes = make_read_string_bytes(d_path)

        def read_mem(addr, sz, cnt, raw=False):
            if addr == p_dir and cnt == 0x8:
                return dir_struct
            offset = addr - p_path
            if 0 <= offset < len(string_bytes):
                return bytes([string_bytes[offset]])
            return bytes([0])

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.opendir.return_value = (0, 99)

        ret_intercept, ret_val = fs_handler.fs_opendir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.opendir.assert_called_once_with(d_path)
        qemu.write_memory.assert_called()


# ---------------------------------------------------------------------------
# Tests: fs_readdir
# ---------------------------------------------------------------------------

class TestFsReaddir:
    def test_reads_directory_entry_file(self, fs_handler, qemu):
        p_dir = 0x7000
        p_out = 0xC000
        p_fs = 99

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_dir, p_out][idx])

        dir_struct = struct.pack("<LL", p_fs, 0)
        qemu.read_memory = mock.Mock(return_value=dir_struct)

        fake_stat = FakeStatResult(S_IFREG | 0o644, size=500)
        fs_handler.model.readdir.return_value = (0, fake_stat, "file.txt")

        ret_intercept, ret_val = fs_handler.fs_readdir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        qemu.write_memory.assert_called()

    def test_reads_directory_entry_dir(self, fs_handler, qemu):
        p_dir = 0x7000
        p_out = 0xC000
        p_fs = 99

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_dir, p_out][idx])
        dir_struct = struct.pack("<LL", p_fs, 0)
        qemu.read_memory = mock.Mock(return_value=dir_struct)

        fake_stat = FakeStatResult(S_IFDIR | 0o755, size=0)
        fs_handler.model.readdir.return_value = (0, fake_stat, "subdir")

        ret_intercept, ret_val = fs_handler.fs_readdir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0

    def test_reads_empty_directory(self, fs_handler, qemu):
        p_dir = 0x7000
        p_out = 0xC000
        p_fs = 99

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_dir, p_out][idx])
        dir_struct = struct.pack("<LL", p_fs, 0)
        qemu.read_memory = mock.Mock(return_value=dir_struct)

        fs_handler.model.readdir.return_value = (0, None, "")

        ret_intercept, ret_val = fs_handler.fs_readdir(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        # write_memory still called with empty entry
        qemu.write_memory.assert_called()


# ---------------------------------------------------------------------------
# Tests: fs_rename
# ---------------------------------------------------------------------------

class TestFsRename:
    def test_renames_file(self, fs_handler, qemu):
        src_addr = 0x5000
        dst_addr = 0x6000
        src_path = "/mnt/old.txt"
        dst_path = "/mnt/new.txt"

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [src_addr, dst_addr][idx])

        src_bytes = make_read_string_bytes(src_path)
        dst_bytes = make_read_string_bytes(dst_path)

        def read_mem(addr, sz, cnt, raw=False):
            if addr >= src_addr and addr < src_addr + len(src_bytes):
                offset = addr - src_addr
                return bytes([src_bytes[offset]])
            if addr >= dst_addr and addr < dst_addr + len(dst_bytes):
                offset = addr - dst_addr
                return bytes([dst_bytes[offset]])
            return bytes([0])

        qemu.read_memory = mock.Mock(side_effect=read_mem)
        fs_handler.model.rename.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_rename(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.rename.assert_called_once_with(src_path, dst_path)


# ---------------------------------------------------------------------------
# Tests: fs_truncate
# ---------------------------------------------------------------------------

class TestFsTruncate:
    def test_truncates_file(self, fs_handler, qemu):
        p_file = 0x7000
        length = 100
        p_fs = 42

        qemu.get_arg = mock.Mock(side_effect=lambda idx: [p_file, length][idx])

        file_struct = struct.pack("<LLB", p_fs, 0, 0)
        qemu.read_memory = mock.Mock(return_value=file_struct)
        fs_handler.model.truncate.return_value = 0

        ret_intercept, ret_val = fs_handler.fs_truncate(qemu, 0x0)
        assert ret_intercept is True
        assert ret_val == 0
        fs_handler.model.truncate.assert_called_once_with(p_fs, length)


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_impl(self):
        # Default impl is HostFSModel; just verify we can construct
        # without error using a mock
        mock_impl = mock.Mock()
        mock_impl.return_value = mock.Mock()
        handler = ZephyrFS(impl=mock_impl)
        mock_impl.assert_called_once()
        assert handler.model is mock_impl.return_value

    def test_custom_impl(self):
        custom_impl = mock.Mock()
        custom_impl.return_value = mock.Mock()
        handler = ZephyrFS(impl=custom_impl)
        custom_impl.assert_called_once()
        assert handler.model is custom_impl.return_value
