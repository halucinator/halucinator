"""
Tests for halucinator.peripheral_models.host_fs.HostFSModel
"""
import os
import shutil
import tempfile
from errno import EBADF, EBUSY, EEXIST, EINVAL, ENOENT, ENOTBLK
from unittest import mock

import pytest

from halucinator.peripheral_models.host_fs import HostFSModel


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """
    Run each test in a temporary directory so VFS operations don't pollute
    the real filesystem.  Reset HostFSModel class-level state between tests.
    """
    monkeypatch.chdir(tmp_path)
    HostFSModel.mount_points = {}
    HostFSModel.open_files = {}
    HostFSModel.current_fd = 1
    HostFSModel.current_dir = 1
    HostFSModel.open_directories = {}
    yield
    # cleanup vfs/storage if they were created
    for d in ("vfs", "storage"):
        p = tmp_path / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


# ---------- HostFSModel.__init__ ----------

def test_init_removes_existing_vfs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vfs = tmp_path / "vfs"
    vfs.mkdir()
    (vfs / "somefile").write_text("data")
    HostFSModel()
    assert not vfs.exists()


def test_init_no_vfs_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Should not raise even when vfs doesn't exist
    HostFSModel()


# ---------- is_valid_path ----------

def test_is_valid_path_inside_vfs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = HostFSModel.__new__(HostFSModel)
    os.makedirs("vfs", exist_ok=True)
    assert model.is_valid_path("vfs/somefile") is True


def test_is_valid_path_outside_vfs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = HostFSModel.__new__(HostFSModel)
    os.makedirs("vfs", exist_ok=True)
    assert model.is_valid_path("/etc/passwd") is False


# ---------- is_valid_mount ----------

def test_is_valid_mount_no_mounts():
    model = HostFSModel.__new__(HostFSModel)
    assert model.is_valid_mount("vfs/test") is False


def test_is_valid_mount_with_mount(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = HostFSModel.__new__(HostFSModel)
    os.makedirs("vfs/mnt", exist_ok=True)
    HostFSModel.mount_points[0] = "/mnt"
    assert model.is_valid_mount("vfs/mnt/file.txt") is True


# ---------- mount ----------

def test_mount_success():
    ret = HostFSModel.mount("/mnt", 0)
    assert ret == 0
    assert 0 in HostFSModel.mount_points


def test_mount_duplicate_fs_type_returns_ebusy():
    HostFSModel.mount("/mnt", 0)
    ret = HostFSModel.mount("/mnt2", 0)
    assert ret == -EBUSY


def test_mount_invalid_path_returns_zero():
    """mount returns 0 for invalid paths (not within vfs)"""
    # force is_valid_path to return False
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        ret = HostFSModel.mount("/mnt", 1)
    assert ret == 0


def test_mount_already_mounted_returns_zero():
    """mount returns 0 when mount point already exists"""
    HostFSModel.mount("/mnt", 0)
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=True):
        ret = HostFSModel.mount("/mnt", 1)
    assert ret == 0


# ---------- open / read / write / close / seek / tell / sync / truncate ----------

def _setup_mount_and_file(content=b"hello world"):
    """Helper: mount fs_type 0 at /mnt and create a file."""
    HostFSModel.mount("/mnt", 0)
    # Create the file in storage
    storage_dir = os.path.realpath("storage/0")
    os.makedirs(storage_dir, exist_ok=True)
    filepath = os.path.join(storage_dir, "test.txt")
    with open(filepath, "wb") as f:
        f.write(content)
    return "/mnt/test.txt"


def test_open_read_close():
    fpath = _setup_mount_and_file(b"hello world")
    ret, fd = HostFSModel.open(fpath, 0x01)  # read mode
    assert ret == 0
    assert fd >= 1
    ret_len, data = HostFSModel.read(fd, 5)
    assert ret_len == 5
    assert data == b"hello"
    ret = HostFSModel.close(fd)
    assert ret == 0


def test_open_write_mode():
    fpath = _setup_mount_and_file(b"old data")
    ret, fd = HostFSModel.open(fpath, 0x02)  # write mode
    assert ret == 0
    written = HostFSModel.write(fd, b"new data")
    assert written == 8
    HostFSModel.close(fd)


def test_open_readwrite_mode():
    fpath = _setup_mount_and_file(b"content")
    ret, fd = HostFSModel.open(fpath, 0x03)  # read+write
    assert ret == 0
    HostFSModel.close(fd)


def test_open_append_mode():
    fpath = _setup_mount_and_file(b"start")
    ret, fd = HostFSModel.open(fpath, 0x23)  # 0x20 append + 0x03 rw
    assert ret == 0
    HostFSModel.close(fd)


def test_open_append_write_mode():
    fpath = _setup_mount_and_file(b"start")
    ret, fd = HostFSModel.open(fpath, 0x22)  # 0x20 append + 0x02 write
    assert ret == 0
    HostFSModel.close(fd)


def test_open_append_read_mode():
    fpath = _setup_mount_and_file(b"start")
    ret, fd = HostFSModel.open(fpath, 0x20)  # 0x20 append + 0x00 read fallback
    assert ret == 0
    HostFSModel.close(fd)


def test_open_create_flag():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret, fd = HostFSModel.open("/mnt/newfile.txt", 0x13)  # 0x10 create + rw
    assert ret == 0
    HostFSModel.close(fd)


def test_open_file_not_found():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret, fd = HostFSModel.open("/mnt/nonexistent.txt", 0x01)
    assert ret == -ENOENT
    assert fd == 0


def test_open_invalid_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        ret, fd = HostFSModel.open("/etc/passwd", 0x01)
    assert ret == -ENOENT
    assert fd == 0


def test_open_no_valid_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        ret, fd = HostFSModel.open("/mnt/file", 0x01)
    assert ret == -ENOENT


def test_read_invalid_fd():
    ret, data = HostFSModel.read(9999, 10)
    assert ret == -EBADF
    assert data == bytes([])


def test_read_unsupported_operation():
    fpath = _setup_mount_and_file(b"data")
    ret, fd = HostFSModel.open(fpath, 0x02)  # write-only
    assert ret == 0
    ret_len, data = HostFSModel.read(fd, 10)
    assert ret_len == 0
    assert data == bytes([])
    HostFSModel.close(fd)


def test_write_invalid_fd():
    ret = HostFSModel.write(9999, b"data")
    assert ret == -EBADF


def test_write_unsupported_operation():
    """Write to a read-only file returns 0."""
    fpath = _setup_mount_and_file(b"data")
    ret, fd = HostFSModel.open(fpath, 0x01)  # read-only (flags & 0x03 == 0x01 -> rb+)
    assert ret == 0
    # rb+ actually supports write, so mock the UnsupportedOperation
    mock_file = mock.MagicMock()
    mock_file.write.side_effect = IOError("not supported")
    # Actually test with the real UnsupportedOperation path
    import io
    mock_file.write.side_effect = io.UnsupportedOperation("not writable")
    HostFSModel.open_files[fd] = mock_file
    ret = HostFSModel.write(fd, b"test")
    assert ret == 0
    HostFSModel.close(fd)


def test_seek_and_tell():
    fpath = _setup_mount_and_file(b"hello world")
    ret, fd = HostFSModel.open(fpath, 0x01)
    assert ret == 0
    assert HostFSModel.tell(fd) == 0
    HostFSModel.seek(fd, 5, os.SEEK_SET)
    assert HostFSModel.tell(fd) == 5
    HostFSModel.close(fd)


def test_seek_invalid_fd():
    assert HostFSModel.seek(9999, 0, 0) == -EBADF


def test_tell_invalid_fd():
    assert HostFSModel.tell(9999) == -EBADF


def test_sync_valid_fd():
    fpath = _setup_mount_and_file(b"data")
    ret, fd = HostFSModel.open(fpath, 0x01)
    assert HostFSModel.sync(fd) == 0
    HostFSModel.close(fd)


def test_sync_invalid_fd():
    assert HostFSModel.sync(9999) == -EBADF


def test_close_invalid_fd_returns_zero():
    assert HostFSModel.close(9999) == 0


def test_truncate_valid_fd():
    fpath = _setup_mount_and_file(b"hello world")
    ret, fd = HostFSModel.open(fpath, 0x03)
    assert ret == 0
    assert HostFSModel.truncate(fd, 5) == 0
    HostFSModel.close(fd)


def test_truncate_invalid_fd():
    assert HostFSModel.truncate(9999, 5) == -EBADF


# ---------- stat / statvfs ----------

def test_stat_existing_file():
    fpath = _setup_mount_and_file(b"data")
    ret, stat_result = HostFSModel.stat(fpath)
    assert ret == 0
    assert stat_result is not None
    assert stat_result.st_size == 4


def test_stat_nonexistent_file():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret, stat_result = HostFSModel.stat("/mnt/nonexist.txt")
    assert ret == -ENOENT
    assert stat_result is None


def test_stat_invalid_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        ret, stat_result = HostFSModel.stat("/etc/passwd")
    assert ret == -ENOENT


def test_stat_no_valid_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        ret, stat_result = HostFSModel.stat("/mnt/file")
    assert ret == -ENOENT


def test_statvfs():
    HostFSModel.mount("/mnt", 0)
    result = HostFSModel.statvfs("/mnt")
    assert hasattr(result, "f_bsize")


# ---------- unmount ----------

def test_unmount_success():
    HostFSModel.mount("/mnt", 0)
    ret = HostFSModel.unmount("/mnt", 0)
    assert ret == 0
    assert 0 not in HostFSModel.mount_points


def test_unmount_not_mounted():
    ret = HostFSModel.unmount("/mnt", 99)
    assert ret == -EINVAL


# ---------- mkdir / opendir / readdir / closedir ----------

def test_mkdir_success():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret = HostFSModel.mkdir("/mnt/newdir")
    assert ret == 0
    assert os.path.isdir("./vfs/mnt/newdir")


def test_mkdir_already_exists():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0/existdir", exist_ok=True)
    ret = HostFSModel.mkdir("/mnt/existdir")
    assert ret == -EEXIST


def test_mkdir_invalid_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        ret = HostFSModel.mkdir("/bad/path")
    assert ret == -ENOENT


def test_mkdir_no_valid_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        ret = HostFSModel.mkdir("/mnt/dir")
    assert ret == -ENOENT


def test_opendir_readdir_closedir():
    HostFSModel.mount("/mnt", 0)
    storage = "storage/0"
    os.makedirs(storage, exist_ok=True)
    # Create files in the storage
    with open(os.path.join(storage, "a.txt"), "w") as f:
        f.write("a")
    with open(os.path.join(storage, "b.txt"), "w") as f:
        f.write("b")

    ret, d_id = HostFSModel.opendir("/mnt")
    assert ret == 0
    assert d_id >= 1

    # Read all entries
    names = []
    while True:
        ret, stat_result, name = HostFSModel.readdir(d_id)
        if name == "":
            break
        names.append(name)
    assert sorted(names) == ["a.txt", "b.txt"]

    assert HostFSModel.closedir(d_id) == 0


def test_opendir_nonexistent():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret, d_id = HostFSModel.opendir("/mnt/nonexist")
    assert ret == -ENOENT
    assert d_id == 0


def test_opendir_invalid_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        ret, d_id = HostFSModel.opendir("/bad")
    assert ret == -ENOENT


def test_opendir_no_valid_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        ret, d_id = HostFSModel.opendir("/mnt")
    assert ret == -ENOENT


def test_readdir_invalid_d_id():
    ret, stat_result, name = HostFSModel.readdir(9999)
    assert ret == -EBADF
    assert stat_result is None
    assert name == ""


def test_closedir_invalid_d_id():
    assert HostFSModel.closedir(9999) == 0


# ---------- unlink ----------

def test_unlink_file():
    fpath = _setup_mount_and_file(b"data")
    assert HostFSModel.unlink(fpath) == 0
    assert not os.path.exists("./vfs" + fpath)


def test_unlink_empty_directory():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0/emptydir", exist_ok=True)
    assert HostFSModel.unlink("/mnt/emptydir") == 0


def test_unlink_nonempty_directory():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0/fulldir", exist_ok=True)
    with open("storage/0/fulldir/file.txt", "w") as f:
        f.write("data")
    ret = HostFSModel.unlink("/mnt/fulldir")
    assert ret == -ENOTBLK


def test_unlink_nonexistent():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret = HostFSModel.unlink("/mnt/nofile")
    assert ret == -ENOENT


def test_unlink_invalid_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        assert HostFSModel.unlink("/bad") == -ENOENT


def test_unlink_no_valid_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        assert HostFSModel.unlink("/mnt/file") == -ENOENT


# ---------- rename ----------

def test_rename_success():
    fpath = _setup_mount_and_file(b"data")
    ret = HostFSModel.rename("/mnt/test.txt", "/mnt/renamed.txt")
    assert ret == 0
    assert os.path.exists("./vfs/mnt/renamed.txt")
    assert not os.path.exists("./vfs/mnt/test.txt")


def test_rename_src_not_found():
    HostFSModel.mount("/mnt", 0)
    os.makedirs("storage/0", exist_ok=True)
    ret = HostFSModel.rename("/mnt/nonexist.txt", "/mnt/dest.txt")
    assert ret == -ENOENT


def test_rename_invalid_src_path():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=False):
        assert HostFSModel.rename("/bad", "/mnt/dest") == -EINVAL


def test_rename_invalid_src_mount():
    with mock.patch.object(HostFSModel, "is_valid_path", return_value=True), \
         mock.patch.object(HostFSModel, "is_valid_mount", return_value=False):
        assert HostFSModel.rename("/mnt/a", "/mnt/b") == -EINVAL


def test_rename_invalid_dst_path():
    _setup_mount_and_file(b"data")
    call_count = [0]
    orig_is_valid_path = HostFSModel.is_valid_path

    def side_effect(self_arg, path):
        call_count[0] += 1
        if call_count[0] <= 1:
            return orig_is_valid_path(self_arg, path)
        return False

    with mock.patch.object(HostFSModel, "is_valid_path", side_effect=side_effect):
        ret = HostFSModel.rename("/mnt/test.txt", "/bad/dest")
    assert ret == -EINVAL


def test_rename_invalid_dst_mount():
    _setup_mount_and_file(b"data")
    call_count = [0]

    def mount_side_effect(self_arg, path):
        call_count[0] += 1
        if call_count[0] <= 1:
            return True
        return False

    with mock.patch.object(HostFSModel, "is_valid_mount", side_effect=mount_side_effect):
        ret = HostFSModel.rename("/mnt/test.txt", "/mnt/dest")
    assert ret == -EINVAL
