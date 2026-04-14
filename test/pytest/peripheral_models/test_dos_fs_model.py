"""
Tests for halucinator.peripheral_models.dos_fs_model
"""
import os
import shutil
import tempfile

import pytest

from halucinator.peripheral_models.dos_fs_model import (
    DosFsModel,
    is_mkdir,
    translate_flags,
)


# ---------- translate_flags ----------

def test_translate_flags_wronly():
    assert translate_flags(0x0001) == os.O_WRONLY


def test_translate_flags_rdwr():
    assert translate_flags(0x0002) == os.O_RDWR


def test_translate_flags_creat():
    assert translate_flags(0x0200) == os.O_CREAT


def test_translate_flags_combined():
    result = translate_flags(0x0201)
    assert result == (os.O_CREAT | os.O_WRONLY)


def test_translate_flags_none():
    assert translate_flags(0x0000) == 0


def test_translate_flags_unknown_bits_ignored():
    # Bits not in bit_flags map should be ignored
    assert translate_flags(0x0800) == 0


# ---------- is_mkdir ----------

def test_is_mkdir_true():
    assert is_mkdir(0x4000) != 0


def test_is_mkdir_false():
    assert is_mkdir(0x0000) == 0


def test_is_mkdir_with_other_bits():
    assert is_mkdir(0x4001) != 0


# ---------- DosFsModel fixture ----------

@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """
    Run each test in tmp directory. Reset DosFsModel state.
    """
    monkeypatch.chdir(tmp_path)
    DosFsModel.readdir = {}
    DosFsModel.fd_table = {}
    # Override localDir to use tmp_path-based location
    DosFsModel.localDir = str(tmp_path / "HALucinator" / "FS")
    yield
    # Close any lingering fds
    for fd in list(DosFsModel.fd_table.keys()):
        try:
            os.close(fd)
        except OSError:
            pass
    DosFsModel.fd_table = {}


# ---------- get_filename ----------

def test_get_filename_exists():
    DosFsModel.fd_table[10] = "/some/path"
    assert DosFsModel.get_filename(10) == "/some/path"


def test_get_filename_missing():
    assert DosFsModel.get_filename(999) is None


# ---------- creat_or_open ----------

def test_creat_or_open_creates_file():
    success, fd = DosFsModel.creat_or_open("/testfile.txt", 0x0200, 0)
    assert success is True
    assert fd != 0xFFFFFFFF
    assert fd in DosFsModel.fd_table
    os.close(fd)


def test_creat_or_open_opens_existing():
    # Create the file first
    success, fd1 = DosFsModel.creat_or_open("/existing.txt", 0x0200, 0)
    assert success is True
    os.close(fd1)
    # Open it again
    success, fd2 = DosFsModel.creat_or_open("/existing.txt", 0x0000, 0)
    assert success is True
    assert fd2 != 0xFFFFFFFF
    os.close(fd2)


def test_creat_or_open_mkdir_mode():
    success, fd = DosFsModel.creat_or_open("/newdir", 0x0000, 0x4000)
    assert success is True
    assert fd != 0xFFFFFFFF
    assert os.path.isdir(DosFsModel.localDir + "/newdir")
    os.close(fd)


def test_creat_or_open_mkdir_existing_dir():
    # Create directory first
    success, fd1 = DosFsModel.creat_or_open("/existdir", 0x0000, 0x4000)
    assert success is True
    os.close(fd1)
    # Open existing directory
    success, fd2 = DosFsModel.creat_or_open("/existdir", 0x0000, 0x4000)
    assert success is True
    assert fd2 != 0xFFFFFFFF
    os.close(fd2)


def test_creat_or_open_empty_name():
    success, fd = DosFsModel.creat_or_open("", 0, 0)
    assert success is True
    assert fd == 0xFFFFFFFF


def test_creat_or_open_whitespace_name():
    success, fd = DosFsModel.creat_or_open("   ", 0, 0)
    assert success is True
    assert fd == 0xFFFFFFFF


def test_creat_or_open_single_char_name():
    success, fd = DosFsModel.creat_or_open("/", 0x0200, 0)
    assert success is True
    # "/" has len 1, not > 1
    assert fd == 0xFFFFFFFF


def test_creat_or_open_oserror():
    """Opening a non-existent file without O_CREAT returns error."""
    success, fd = DosFsModel.creat_or_open("/nofile.txt", 0x0000, 0)
    assert success is True
    assert fd == 0xFFFFFFFF


# ---------- read / write / close ----------

def test_read_write_close():
    success, fd = DosFsModel.creat_or_open("/rw.txt", 0x0202, 0)  # RDWR | CREAT
    assert success is True
    data = b"hello world"
    DosFsModel.write(fd, data)
    os.lseek(fd, 0, os.SEEK_SET)
    result = DosFsModel.read(fd, len(data))
    assert result == data
    DosFsModel.close(fd)


# ---------- delete ----------

def test_delete_file():
    success, fd = DosFsModel.creat_or_open("/delme.txt", 0x0200, 0)
    os.close(fd)
    drive = "/"
    ret = DosFsModel.delete(None, "delme.txt", drive)
    assert ret == 0


def test_delete_directory():
    success, fd = DosFsModel.creat_or_open("/deldir", 0x0000, 0x4000)
    os.close(fd)
    drive = "/"
    ret = DosFsModel.delete(None, "deldir", drive)
    assert ret == 0


def test_delete_nonempty_directory():
    success, fd = DosFsModel.creat_or_open("/fulldir/file.txt", 0x0200, 0)
    os.close(fd)
    drive = "/"
    ret = DosFsModel.delete(None, "fulldir", drive)
    assert ret == 0xFFFFFFFF


def test_delete_nonexistent():
    drive = "/"
    # Create the localDir + drive directory
    os.makedirs(DosFsModel.localDir + drive, exist_ok=True)
    ret = DosFsModel.delete(None, "ghost.txt", drive)
    assert ret == 0xFFFFFFFF


def test_delete_no_driver():
    ret = DosFsModel.delete(None, "/path", None)
    assert ret == 0


# ---------- fio_move ----------

def test_fio_move():
    success, fd = DosFsModel.creat_or_open("/moveme.txt", 0x0200, 0)
    os.close(fd)
    old_path = DosFsModel.fd_table[fd]
    DosFsModel.fio_move(fd, "/moved.txt")
    new_path = DosFsModel.localDir + "/moved.txt"
    assert os.path.exists(new_path)
    assert not os.path.exists(old_path)


# ---------- fio_time_set ----------

def test_fio_time_set():
    success, fd = DosFsModel.creat_or_open("/timefile.txt", 0x0200, 0)
    os.close(fd)
    DosFsModel.fio_time_set(fd, 1000000, 2000000)
    stat = os.stat(DosFsModel.fd_table[fd])
    assert int(stat.st_atime) == 1000000
    assert int(stat.st_mtime) == 2000000


# ---------- fio_read ----------

def test_fio_read():
    success, fd = DosFsModel.creat_or_open("/readinfo.txt", 0x0202, 0)
    os.write(fd, b"abcdefghij")  # 10 bytes
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = DosFsModel.fio_read(fd)
    # st_size - 1 - cur = 10 - 1 - 0 = 9
    assert remaining == 9


# ---------- fio_seek / fio_where ----------

def test_fio_seek_and_where():
    success, fd = DosFsModel.creat_or_open("/seekfile.txt", 0x0202, 0)
    os.write(fd, b"0123456789")
    os.lseek(fd, 0, os.SEEK_SET)
    assert DosFsModel.fio_where(fd) == 0
    DosFsModel.fio_seek(fd, 5)
    assert DosFsModel.fio_where(fd) == 5


# ---------- fio_read_dir ----------

def test_fio_read_dir():
    # Create a directory with files
    success, fd = DosFsModel.creat_or_open("/dirread", 0x0000, 0x4000)
    os.close(fd)

    # Create files inside
    for name in ["aa.txt", "bb.txt"]:
        s, f = DosFsModel.creat_or_open("/dirread/" + name, 0x0200, 0)
        os.close(f)

    # Re-open the directory
    s2, dfd = DosFsModel.creat_or_open("/dirread", 0x0000, 0x4000)

    # Init read
    result1 = DosFsModel.fio_read_dir(dfd, init=True)
    assert result1 is not None
    assert isinstance(result1, bytes)

    # Continue reading
    result2 = DosFsModel.fio_read_dir(dfd, init=False)
    assert result2 is not None

    # End of directory
    result3 = DosFsModel.fio_read_dir(dfd, init=False)
    assert result3 is None

    os.close(dfd)


def test_fio_read_dir_empty():
    success, fd = DosFsModel.creat_or_open("/emptydir", 0x0000, 0x4000)
    os.close(fd)
    s2, dfd = DosFsModel.creat_or_open("/emptydir", 0x0000, 0x4000)
    result = DosFsModel.fio_read_dir(dfd, init=True)
    assert result is None
    os.close(dfd)


# ---------- fio_fstat_get ----------

def test_fio_fstat_get():
    success, fd = DosFsModel.creat_or_open("/statfile.txt", 0x0202, 0)
    os.write(fd, b"data")
    result = DosFsModel.fio_fstat_get(fd)
    assert "st_dev" in result
    assert "st_nlink" in result
    assert "st_size" in result
    assert "st_blksize" in result
    assert "st_blocks" in result
    assert "st_attrib" in result
    assert "st_mode" in result
    assert "st_atime" in result
    assert "st_mtime" in result
    assert result["st_attrib"] == 0
    os.close(fd)


# ---------- fio_rename ----------

def test_fio_rename():
    success, fd = DosFsModel.creat_or_open("/renameme.txt", 0x0200, 0)
    os.close(fd)
    old_path = DosFsModel.fd_table[fd]
    DosFsModel.fio_rename(fd, "newname.txt")
    assert DosFsModel.fd_table[fd].endswith("newname.txt")
    assert os.path.exists(DosFsModel.fd_table[fd])
    assert not os.path.exists(old_path)
