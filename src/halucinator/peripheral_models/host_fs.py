# Copyright 2019-2021 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.

from halucinator.peripheral_models.peripheral import requires_tx_map, requires_rx_map, requires_interrupt_map
from halucinator.peripheral_models import peripheral_server
from collections import defaultdict
import os
import io
import shutil
import logging
from os import sys, path
from stat import S_ISDIR
from errno import *
log = logging.getLogger(__name__)


# Register the pub/sub calls and methods that need mapped
# TODO Convert to class that can be instantiated with its parameters
# without requiring every function to be a classmethod
@peripheral_server.peripheral_model
class HostFSModel(object):
    # keep states here
    mount_points = {}
    open_files = {}
    current_fd = 1
    current_dir = 1
    open_directories = {}

    def __init__(self):
        """Initialization of HostFSModel class
        """

        # Make sure our VFS is clean of any lingering
        # symlinks before beginning
        try:
            shutil.rmtree("vfs")
        except OSError:
            pass

    def is_valid_path(self, path):
        """Helper function to verify if a file/folder is within the VFS folder or not.

        :param path: Relative or absolute file/folder path to check
        :type path: str
        """
        abs_path = os.path.abspath(path)
        common_prefix_verify = os.path.abspath("./vfs")
        common_prefix = os.path.commonpath([abs_path, common_prefix_verify])
        return (common_prefix == common_prefix_verify)

    def is_valid_mount(self, mp):
        """Helper function to verify if a file/folder is within the VFS folder or not.

        :param path: Relative or absolute file/folder path to check
        :type path: str
        """
        abs_path = os.path.abspath(mp)
        for storage in self.mount_points:
            common_prefix_verify = os.path.abspath("./vfs" + self.mount_points[storage])
            common_prefix = os.path.commonpath([abs_path, common_prefix_verify])
            if (common_prefix == common_prefix_verify):
                return True
        return False

    @classmethod
    def mount(self, mount_path, fs_type):
        """Mounts a specified FS type to mount_path

        :param mount_path: VFS mount path for filesystem
        :type mount_path: str
        :param fs_type: Filesystem ID to mount from storage/
        :type fs_type: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if (fs_type in self.mount_points):
            return -EBUSY
        print("mount ", mount_path, fs_type)
        dir_path = os.path.realpath("storage/" + str(fs_type))
        #
        try:
            os.makedirs("vfs", exist_ok=True)
        except OSError:
            pass

        try:
            os.makedirs("storage/" + str(fs_type), exist_ok=True)
        except OSError:
            pass

        if not self.is_valid_path(self, "vfs" + mount_path):
            return 0

        if self.is_valid_mount(self, "vfs" + mount_path):
            return 0

        try:
            os.symlink(dir_path, "vfs" + mount_path, target_is_directory=True)
        except OSError:
            pass
        self.mount_points[fs_type] = mount_path

        return 0

    @classmethod
    def open(self, f_path, flags):
        """Opens a specified file path from VFS, with a specific open mode

        :param f_path: VFS file path
        :type f_path: str
        :param flags: Open mode flags
        :type flags: int
        :return: A tuple containing the return value (0 on success, -ERRNO on errors)
                 and on success, a file handle.
        :rtype: int, int
        """
        if not self.is_valid_path(self, "./vfs" + f_path):
            return (-ENOENT, 0)

        if not self.is_valid_mount(self, "./vfs" + f_path):
            return (-ENOENT, 0)

        # TODO make sure files can't be created in /

        try:
            if (not path.exists("./vfs" + f_path) and (flags & 0x10)):
                open("./vfs" + f_path, "wb+").close()
            if (flags & 0x20):
                if ((flags & 0x03) == 0x03):
                    f = open("./vfs" + f_path, "rb+")
                elif ((flags & 0x03) == 0x02):
                    f = open("./vfs" + f_path, "wb")
                else:
                    f = open("./vfs" + f_path, "rb+")

                f.seek(0, os.SEEK_END)
            else:
                if ((flags & 0x03) == 0x03):
                    f = open("./vfs" + f_path, "rb+")
                elif ((flags & 0x03) == 0x02):
                    f = open("./vfs" + f_path, "wb")
                else:
                    f = open("./vfs" + f_path, "rb+")

                f.seek(0)
        except FileNotFoundError:
            return (-ENOENT, 0)

        self.open_files[self.current_fd] = f
        self.current_fd += 1
        return (0, (self.current_fd - 1))

    @classmethod
    def read(self, f_id, f_size):
        """Reads from a VFS file handle, with a specified size

        :param f_id: VFS file handle
        :type f_id: int
        :param f_size: Size to read from handle
        :type f_size: int
        :return: A tuple containing the return value (0 on success, -ERRNO on errors)
                 and on success, contents read from the file
        :rtype: int, int
        """
        if f_id not in self.open_files:
            return -EBADF, bytes([])

        try:
            data = self.open_files[f_id].read(f_size)
            return len(data), data
        except io.UnsupportedOperation:
            return 0, bytes([])

    @classmethod
    def write(self, f_id, f_data):
        """Writes to a VFS file handle, with specified data

        :param f_id: VFS file handle
        :type f_id: int
        :param f_data: Data to write to VFS file
        :type f_data: bytes
        :return: Number of bytes written on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return -EBADF

        try:
            return self.open_files[f_id].write(f_data)
        except io.UnsupportedOperation:
            return 0

    @classmethod
    def statvfs(self, f_path):
        """Stats VFS mount information.

        :param f_path: VFS mount path
        :type f_path: int
        :return: A tuple of an errcode (0 on success, -ERRNO on errors) and statvfs results.
        :rtype: int, os.statvfs_result
        """
        # TODO invalid paths?
        return os.statvfs("./vfs" + f_path)

    @classmethod
    def stat(self, f_path):
        """Stats a VFS file/directory

        :param f_path: VFS file/directory path
        :type f_path: int
        :return: A tuple of an errcode (0 on success, -ERRNO on errors) and stat results.
        :rtype: int, os.stat_result
        """
        if not self.is_valid_path(self, "./vfs" + f_path):
            return -ENOENT, None

        if not self.is_valid_mount(self, "./vfs" + f_path):
            return (-ENOENT, None)

        try:
            return 0, os.stat("./vfs" + f_path)
        except FileNotFoundError:
            return -ENOENT, None

    @classmethod
    def close(self, f_id):
        """Closes a VFS file handle

        :param f_id: VFS file handle
        :type f_id: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return 0

        self.open_files[f_id].close()
        del self.open_files[f_id]
        return 0
        #wipe id to zero

    @classmethod
    def seek(self, f_id, f_pos, f_whence):
        """Seeks a VFS file handle

        :param f_id: VFS file handle
        :type f_id: int
        :param f_pos: Relative offset to seek to
        :type f_pos: int
        :param f_whence: Whence offset to seek from
        :type f_whence: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return -EBADF

        self.open_files[f_id].seek(f_pos, f_whence)
        return 0

    @classmethod
    def unmount(self, mount_path, fs_type):
        """Unmounts a specified path

        :param mount_path: VFS mount path
        :type mount_path: str
        :param fs_type: Filesystem type to unmount
        :type fs_type: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if fs_type not in self.mount_points:
            return -EINVAL

        os.unlink("./vfs" + self.mount_points[fs_type])
        del self.mount_points[fs_type]
        return 0

    @classmethod
    def tell(self, f_id):
        """Gets VFS file handle seek position

        :param f_id: VFS file handle
        :type f_id: int
        :return: File position on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return -EBADF

        return self.open_files[f_id].tell()

    @classmethod
    def sync(self, f_id):
        """Syncs a VFS file handle (but actually it does nothing)

        :param f_id: VFS file handle
        :type f_id: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return -EBADF

        return 0 # self.open_files[f_id].flush()

    @classmethod
    def closedir(self, d_id):
        """Closes a VFS directory handle

        :param d_id: VFS directory handle
        :type d_id: int
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if d_id not in self.open_directories:
            return 0
        del self.open_directories[d_id]
        return 0

    @classmethod
    def mkdir(self, d_path):
        """Creates a VFS directory

        :param d_path: VFS directory path
        :type d_path: str
        :return: 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if not self.is_valid_path(self, "./vfs" + d_path):
            return -ENOENT

        if not self.is_valid_mount(self, "./vfs" + d_path):
            return (-ENOENT)

        # TODO make sure folders can't be created in /

        if (path.exists("./vfs" + d_path)):
            return -EEXIST

        os.mkdir("./vfs" + d_path)
        return 0

    @classmethod
    def opendir(self, d_path):
        """Opens a VFS directory and returns a directory handle

        :param d_path: VFS directory path to open
        :type d_path: str
        :return: Returns an error code (0 on success, -ERRNO on errors) and
                 on success, a directory handle.
        :rtype: int, int
        """
        if not self.is_valid_path(self, "./vfs" + d_path):
            return (-ENOENT, 0)

        if not self.is_valid_mount(self, "./vfs" + d_path):
            return (-ENOENT, 0)

        if (not path.exists("./vfs" + d_path)):
            return (-ENOENT, 0)

        self.open_directories[self.current_dir] = os.scandir("./vfs" + d_path)
        self.current_dir += 1
        return (0, (self.current_dir - 1))

    @classmethod
    def readdir(self, d_id):
        """Reads next information from a VFS directory handle

        :param d_id: VFS directory handle
        :type d_id: int
        :return: Returns a tuple: An errcode (0 on success, -ERRNO on errors),
                 DirEntry information on success (or None on failure),
                 and the associated entry name str.
        :rtype: int, DirEntry, str
        """
        if d_id not in self.open_directories:
            return -EBADF, None, ""
        try:
            d_entry = next(self.open_directories[d_id])
        except StopIteration:
            return 0, None, ""

        try:
            return 0, d_entry.stat(), d_entry.name
        except FileNotFoundError:
            return -ENOENT, None, ""

    @classmethod
    def unlink(self, f_path):
        """Unlinks a VFS file/directory

        :param f_path: VFS file/directory path to unlink
        :type f_path: str
        :return: Returns 0 on success, -ERRNO on errors
        :rtype: int
        """
        if not self.is_valid_path(self, "./vfs" + f_path):
            return -ENOENT

        if not self.is_valid_mount(self, "./vfs" + f_path):
            return (-ENOENT)

        if (not path.exists("./vfs" + f_path)):
            return -ENOENT
        try:
            os.unlink("./vfs" + f_path)
        except (IsADirectoryError, PermissionError):
            dir_path = os.path.realpath("./vfs" + f_path)
            try:
                os.rmdir(dir_path)
            except OSError:
                return -ENOTBLK
        return 0

    @classmethod
    def rename(self, src, dst):
        """Renames a VFS file/directory. If the destination already exists
           and is valid (and contains no children, if destination is a directory),
           it will be clobbered and replaced by source file.

        :param src: Source VFS file/directory path to be renamed
        :type src: str
        :param dst: Destination VFS file/directory path
        :type dst: str
        :return: Returns 0 on success, -ERRNO on errors
        :rtype: int
        """
        if not self.is_valid_path(self, "./vfs" + src):
            return -EINVAL

        if not self.is_valid_mount(self, "./vfs" + src):
            return (-EINVAL)

        if not self.is_valid_path(self, "./vfs" + dst):
            return -EINVAL

        if not self.is_valid_mount(self, "./vfs" + dst):
            return (-EINVAL)

        # TODO make sure files can't be created in /

        if (not path.exists("./vfs" + src)):
            return -ENOENT # -EINVAL?

        try:
            os.rename("./vfs" + src, "./vfs" + dst)
        except OSError:
            return -ENOTBLK
        return 0
    @classmethod
    def truncate(self, f_id, length):
        """Truncates a VFS file handle to a specified length.

        :param f_id: VFS file handle
        :type f_id: int
        :param length: Length to truncate to
        :type length: int
        :return: Returns an errcode, 0 on success, -ERRNO on errors.
        :rtype: int
        """
        if f_id not in self.open_files:
            return -EBADF

        self.open_files[f_id].truncate(length)

        return 0
