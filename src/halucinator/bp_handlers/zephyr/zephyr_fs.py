# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

# Created by BYU Capstone Team 44 2020-2021
# Project sponsored by National Technology & Engineering Solutions of Sandia, LLC (NTESS).

import logging
import os
import struct
from os import path, sys
from stat import S_ISDIR

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.host_fs import HostFSModel

log = logging.getLogger(__name__)

from ... import hal_log

hal_log = hal_log.getHalLogger()


class ZephyrFS(BPHandler):

    def read_string(self, qemu, addr):
        """Helper function to read strings from QEMU memory

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param addr: Address to read string from
        :type addr: int
        :return: String read from address
        :rtype: str
        """
        p_str = addr
        p_data = bytes([])
        while 1:
            temp = qemu.read_memory(p_str, 1, 1, raw=True)
            if temp[0] == 0:
                break
            p_data += temp
            p_str += 1

        return p_data.decode("utf-8")


    def __init__(self, impl=HostFSModel):
        """Initialization of ZephyrFS class and tx/rx buffers

        :param impl: FS Peripheral Model, defaults to HostFSModel
        :type impl: HostFSModel, optional
        """
        self.model = impl()

    @bp_handler(['flash_area_stub'])
    def flash_area_stub(self, qemu, bp_addr):
        """Stub handler for flash_area_* functions

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        log.info("Flash Stub Called")
        return True, 0

    @bp_handler(['fs_mount'])
    def fs_mount(self, qemu, bp_addr):
        """Mounts a virtual storage medium to the host virtual filesystem.
           All FS calls to that mount point will correspond with the appropriate
           storage directory in `<host working dir>/storage/`.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_char = qemu.get_arg(0)
        self.mount = p_char
        data = qemu.read_memory(p_char, 1, 0x20, raw=True)
        data_unpack = struct.unpack("<LLLLLLLL", data)
        mnt_path = self.read_string(qemu, data_unpack[3])
        r_val = self.model.mount(mnt_path, data_unpack[5])
        log.info(f"fs_mount called {mnt_path}" )

        return True, r_val

    @bp_handler(['fs_statvfs'])
    def fs_statvfs(self, qemu, bp_addr):
        """fs_statvfs breakpoint handler, polls host FS for filesystem size information.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_out = qemu.get_arg(1)
        p_path = qemu.get_arg(0)

        stat_path = self.read_string(qemu, p_path)
        stat_info = self.model.statvfs(stat_path)
        out = struct.pack("<LLLL", stat_info.f_bsize, stat_info.f_frsize, stat_info.f_blocks, stat_info.f_bfree)
        qemu.write_memory(p_out, 1, out, len(out), raw=True)
        log.info(f"fs_statvfs called with arguments: {stat_path}, {stat_info}")
        return True, 0

    @bp_handler(['fs_stat'])
    def fs_stat(self, qemu, bp_addr):
        """fs_stat breakpoint handler, polls host FS for file/folder information.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_out = qemu.get_arg(1)
        p_path = qemu.get_arg(0)

        stat_path = self.read_string(qemu, p_path)
        retval, info = self.model.stat(stat_path)
        if info is not None:
            out = struct.pack("<B259sL", 1 if S_ISDIR(info.st_mode) else 0, stat_path.encode("utf-8"), 0 if S_ISDIR(info.st_mode) else info.st_size)
            qemu.write_memory(p_out, 1, out, len(out), raw=True)

        log.info("fs_stat called")
        return True, retval

    @bp_handler(['fs_unlink'])
    def fs_unlink(self, qemu, bp_addr):
        """fs_unlink breakpoint handler, removes a file/folder in VFS if existing

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_path = qemu.get_arg(0)

        f_path = self.read_string(qemu, p_path)
        ret = self.model.unlink(f_path)

        log.info(f"fs_unlink called {f_path}")
        return True, ret

    @bp_handler(['fs_open'])
    def fs_open(self, qemu, bp_addr):
        """fs_open breakpoint handler, returns a handle for Host VFS files if existing,
           with mode flags specified.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        p_path = qemu.get_arg(1)

        f_path = self.read_string(qemu, p_path)

        open_flags = qemu.get_arg(2)
        data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", data)
        result, p_fs = self.model.open(f_path, open_flags)
        p_mount = self.mount
        flags = open_flags
        data = struct.pack("<LLB", p_fs, p_mount, flags)
        qemu.write_memory(p_file, 1, data, len(data), raw=True)
        log.info(f"fs_open called: {hex(p_file)}, {hex(p_fs)}, {data}, {hex(p_mount)}, {hex(flags)}, {f_path}")
        return True, result

    @bp_handler(['fs_read'])
    def fs_read(self, qemu, bp_addr):
        """fs_read breakpoint handler, reads from a file handle (if valid) to a specified
           address, returns the number of bytes read to r0.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        p_dst = qemu.get_arg(1)
        size = qemu.get_arg(2)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        result, data = self.model.read(p_fs, size)
        if len(data) > 0:
            qemu.write_memory(p_dst, 1, data, len(data), raw=True)

        log.info(f"fs_read called: {hex(p_dst)}, {size}, {data}")
        return True, result

    @bp_handler(['fs_write'])
    def fs_write(self, qemu, bp_addr):
        """fs_write breakpoint handler, writes to a file handle (if valid)
           from a specified address, returns the number of bytes written to r0.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        write_data = qemu.get_arg(1)
        amount_to_write = qemu.get_arg(2)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        data = qemu.read_memory(write_data, 1, amount_to_write, raw=True)
        amount_written = self.model.write(p_fs, data)

        log.info("fs_write called")
        return True, amount_written

    @bp_handler(['fs_seek'])
    def fs_seek(self, qemu, bp_addr):
        """fs_seek breakpoint handler, seeks a file handle in r0 (if valid) to
           a specified offset, relative to a specified `whence` parameter in r1.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_offset = qemu.get_arg(1)
        seek_flag = qemu.get_arg(2)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        result = self.model.seek(p_fs, f_offset, seek_flag)
        log.info("fs_seek called")
        return True, result

    @bp_handler(['fs_close'])
    def fs_close(self, qemu, bp_addr):
        """fs_close breakpoint handler, closes a file handle in r0 (if valid)
           and flushes file contents to disk.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        result = self.model.close(p_fs)
        p_fs = 0
        p_mount = 0
        data = struct.pack("<LLB", p_fs, p_mount, flags)
        qemu.write_memory(p_file, 1, data, len(data), raw=True)
        log.info("fs_close called")
        return True, result

    @bp_handler(['fs_unmount'])
    def fs_unmount(self, qemu, bp_addr):
        """fs_unmount breakpoint handler, removes Host VFS symlinks for the associated
           storage mount.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_char = qemu.get_arg(0)
        self.mount = p_char
        data = qemu.read_memory(p_char, 1, 0x20, raw=True)
        data_unpack = struct.unpack("<LLLLLLLL", data)
        mnt_path = self.read_string(qemu, data_unpack[3])
        result = self.model.unmount(mnt_path, data_unpack[5])
        log.info("fs_unmount called")
        return True, result

    # returns the position of
    # the cursor in the current file
    @bp_handler(['fs_tell'])
    def fs_tell(self, qemu, bp_addr):
        """returns the position of the cursor in the current file

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        f_pos = self.model.tell(p_fs)
        log.info("fs_tell called")
        return True, f_pos

    @bp_handler(['fs_sync'])
    def fs_sync(self, qemu, bp_addr):
        """fs_sync breakpoint handler, does nothing because Zephyr does nothing.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        retval = self.model.sync(p_fs)
        log.info("fs_sync called")
        return True, retval

    @bp_handler(['fs_closedir'])
    def fs_closedir(self, qemu, bp_addr):
        """fs_closedir breakpoint handler, closes a directory handle (if valid).

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_data = qemu.read_memory(p_file, 1, 0x8, raw=True)
        p_fs, p_mount = struct.unpack("<LL", f_data)
        retval = self.model.closedir(p_fs)
        log.info("fs_closedir called")
        return True, retval

    @bp_handler(['fs_mkdir'])
    def fs_mkdir(self, qemu, bp_addr):
        """fs_mkdir breakpoint handler, creates a folder on a mounted filesystem.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_path = qemu.get_arg(0)
        d_path = self.read_string(qemu, p_path)
        retval = self.model.mkdir(d_path)
        log.info("fs_mkdir called")
        return True, retval

    @bp_handler(['fs_opendir'])
    def fs_opendir(self, qemu, bp_addr):
        """fs_opendir breakpoint handler, opens a directory handle for a specified path.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_dir = qemu.get_arg(0)
        p_path = qemu.get_arg(1)

        d_data = qemu.read_memory(p_dir, 1, 0x8, raw=True)
        p_fs, p_mount = struct.unpack("<LL", d_data)
        d_path = self.read_string(qemu, p_path)

        retval, p_fs = self.model.opendir(d_path)

        d_data = struct.pack("<LL", p_fs, p_mount)
        qemu.write_memory(p_dir, 1, d_data, len(d_data), raw=True)


        log.info(f"fs_opendir called: {d_path}")
        return True, retval

    @bp_handler(['fs_readdir'])
    def fs_readdir(self, qemu, bp_addr):
        """fs_readdir breakpoint handler, reads next information from a directory handle.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_dir = qemu.get_arg(0)
        p_out = qemu.get_arg(1)

        d_data = qemu.read_memory(p_dir, 1, 0x8, raw=True)
        p_fs, p_mount = struct.unpack("<LL", d_data)

        retval, info, path = self.model.readdir(p_fs)
        if info is not None:
            out = struct.pack("<B259sL", 1 if S_ISDIR(info.st_mode) else 0, path.encode("utf-8"), 0 if S_ISDIR(info.st_mode) else info.st_size)
            qemu.write_memory(p_out, 1, out, len(out), raw=True)
        else:
            out = struct.pack("<B259sL", 0, "".encode("utf-8"), 0)
            qemu.write_memory(p_out, 1, out, len(out), raw=True)

        log.info(f"fs_readdir called: {path}, {retval}")
        return True, retval

    @bp_handler(['fs_rename'])
    def fs_rename(self, qemu, bp_addr):
        """fs_rename breakpoint handler, renames a file from source path to destination path.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_path_src = qemu.get_arg(0)
        p_path_dst = qemu.get_arg(1)

        path_src = self.read_string(qemu, p_path_src)
        path_dst = self.read_string(qemu, p_path_dst)

        retval = self.model.rename(path_src, path_dst)

        log.info(f"fs_rename called: {path_src}, {path_dst}, {retval}")
        return True, retval


    @bp_handler('fs_truncate')
    def fs_truncate(self, qemu, bp_addr):
        """fs_truncate breakpoint handler, truncates or expands a file handle's file to a
           specified length.

        :param qemu: Firmware Emulator
        :type qemu: Avatar QEMU Target
        :param bp_addr: Breakpoint handler address
        :type bp_addr: tuple
        :return: ‘False’ indicates that the firmware should continue execution from
            the breakpoint and ignore the return value of this function, ‘True’
            indicates that the firmware should use the Integer return value of this
            function instead, Integer return value provides the replacement return
            value if Boolean is ‘True’
        :rtype: boolean, int
        """
        p_file = qemu.get_arg(0)
        f_data = qemu.read_memory(p_file, 1, 0x9, raw=True)
        p_fs, p_mount, flags = struct.unpack("<LLB", f_data)
        length = qemu.get_arg(1)
        retval = self.model.truncate(p_fs, length)
        log.info("fs_truncate called")
        return True, retval
