'''bp_handlers for the dos filesystem'''
import logging
import types

from halucinator.bp_handlers.vxworks.ios_dev import IosDev
from halucinator.peripheral_models.dos_fs_model import DosFsModel
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

class YafFsLib(BPHandler):
    '''
        This implements the YAFFS intercepts
        Usage:
           - class: halucinator.bp_handlers.vxworks.yaf_fs.YafFsLib
             class_args: (optional) {dd_dirent_offset: Offset to dir entry in structure)
             function:
             symbol: <BOARD_SPECIFIC> or
             addr:
    '''
    def __init__(self, impl=DosFsModel,dd_dirent_offset=8):
        self.model = impl
        self.dd_dirent_offset = dd_dirent_offset

    def fio_move(self, qemu, bp_addr, fd, arg):
        '''
            Move file
        '''
        new_path= qemu.read_string(arg)
        self.model.fio_move(fd, new_path)
        return True, 0

    def fio_time_set(self, qemu, bp_addr, fd, time_struct):
        '''
            Sets time on file
        '''
        # TODO: Parameterize
        # **BINARY DEPENDANT**
        # time_struct = actime
        # time_struct + 4 = modtime

        log.debug("fio_time_set")
        atime = int.from_bytes(qemu.read_memory(time_struct, 1, 4,raw=True), "little")
        modtime = int.from_bytes(qemu.read_memory(time_struct + 4, 1, 4, raw=True), "little")
        self.model.fio_time_set(fd,atime, modtime)
        return True, 0

    def fio_attrib_set(self, qemu, bp_addr,fd,st_attrib):
        '''
            0x01   read-only
            0x02   hidden
            0x04   system file
            0x08   volume label
            0x10   sub-directory
            0x20   archive file
        '''


    def fio_rename(self, qemu, bp_addr, fd, p_new_name):
        '''
            Rename file
        '''
        log.debug("fio_rename")
        new_name = qemu.read_string(p_new_name)
        self.model.fio_rename(fd, new_name)
        return True,0

    def fio_read(self, qemu, bp_addr, fd, d_size):
        '''
            Read d_size bytes from file
        '''
        log.debug("FIOREAD")
        rem = self.model.fio_read(fd)
        qemu.write_memory(d_size, 4,rem)
        return True, 0

    def fio_read_dir(self, qemu, bp_addr, fd, p_dir):
        '''
            Read files in directory
        '''
        log.debug("FIOREADDIR")
        dd_dirent_offset = 8
        dd_dirent = p_dir + self.dd_dirent_offset

        # redir is initiated on fd
        if fd in self.model.readdir.keys():
            # check to see if we need to return null
            ret_val = self.model.fio_read_dir(fd, False)
            if ret_val is not None:
                #write to arg in memory
                for i, r_val in enumerate(ret_val):
                    qemu.write_memory(dd_dirent + i, 1, r_val)
                # Delimit
                qemu.write_memory(dd_dirent + i+1, 1, 0x00)
                return True, 0
        # first time
        else:
            ret_val = self.model.fio_read_dir(fd, True)
            if ret_val:
                #write to arg in memory
                for i, r_val in enumerate(ret_val):
                    qemu.write_memory(dd_dirent + i, 1, r_val)
                return True, 0

        return True, None

    def fio_where(self, qemu, bp_addr, fd, arg):
        '''
            fio_where
        '''
        log.debug("FIOWHERE")
        return True, self.model.fio_where(fd)

    def fio_seek (self, qemu, bp_addr, fd, arg):
        '''
            fio_seek
        '''
        log.debug('FIOSEEK')
        self.model.fio_seek(fd, arg)
        return True, 0

    def fio_fstat_get(self, qemu, bp_addr, fd, p_stat):
        '''
            fstat_get
        '''
        # TODO: make not binary dependent by parameterizing
        #     **BINARY DEPENDANT**
        #     (int *pFd,int *p_stat)
        #     p_stat + 0x0 = st_dev
        #     p_stat + 0xa = st_nlink
        #     p_stat + 0x14 = st_size
        #     p_stat + 0x24 = st_blksize
        #     p_stat + 0x28 = st_blocks
        #     p_stat + 0x2c = st_attrib
        #     p_stat + 0x8 = st_mode
        #     #Missing file date info
        #     p_stat + 0x18 = st_atime
        #     p_stat + 0x1c = st_mtime

        log.debug('FIOFSTATGET: %s' % self.model.get_filename(fd))

        stat= self.model.fio_fstat_get(fd)

        qemu.write_memory(p_stat + 0x0, self.model.st_dev, stat["st_dev"])
        qemu.write_memory(p_stat + 0x8, self.model.st_mode, stat["st_mode"])
        qemu.write_memory(p_stat + 0xa, self.model.st_nlink, stat["st_nlink"])
        qemu.write_memory(p_stat + 0x14, self.model.st_size, stat["st_size"])
        qemu.write_memory(p_stat + 0x18, self.model.st_atime_32, stat["st_atime"])
        qemu.write_memory(p_stat + 0x1c, self.model.st_mtime_32, stat["st_mtime"])
        qemu.write_memory(p_stat + 0x24, self.model.st_blksize, stat["st_blksize"])
        qemu.write_memory(p_stat + 0x28, self.model.st_blocks, stat["st_blocks"])
        qemu.write_memory(p_stat + 0x2c, self.model.st_blocks, stat["st_attrib"])

        return True, 0

    @bp_handler(['attrib'])
    def attrib(self, qemu, bp_addr):
        '''
            STATUS attrib(
                const char * fileName,	/* file or dir name on which to change flags */
                const char * attr		/* flag settings to change */
            )
            0x01   read-only
            0x02   hidden
            0x04   system file
            0x08   volume label
            0x10   sub-directory
            0x20   archive file
        '''
        fileName = qemu.read_string(qemu.get_arg(0))
        attr = qemu.read_string(qemu.get_arg(1))
        log.debug("#######################")
        log.debug("yaffs attrib")
        log.debug("fileName: %s, attr flags: %s\n", fileName, attr)
        log.debug("#######################")
        return True, 0


    @bp_handler(['delete'])
    def delete(self, qemu, bp_addr):
        '''
            delete break point handler
        '''
        log.debug("#######################")
        log.debug("yaffs delete")
        log.debug("#######################")
        drv = qemu.get_arg(0)
        path = qemu.read_string(qemu.get_arg(1))
        drive = IosDev.get_driver(drv)
        return True,self.model.delete(drv, path, drive)

    @bp_handler(['create'])
    def create(self, qemu, bp_addr):
        '''
            create break point handler
        '''
        log.debug("#######################")
        log.debug("yaffs create ")
        log.debug("#######################")
        drv = qemu.get_arg(0)
        drive = IosDev.get_driver(drv)
        name = qemu.read_string(qemu.get_arg(1))
        name = drive + name
        flags = qemu.get_arg(2) | 600
        mode = 0x8000
        return self.model.creat_or_open(name, flags, mode)

    @bp_handler(['open'])
    def open(self, qemu, bp_addr):
        '''
            open break point handler
        '''
        # PARAMETERS:
        # pointer to volume descriptor */
        #     char *	pPath,	/* dosFs full path/filename */
        #     int		flags,	/* file open flags */
        #     int		mode	/* file open permissions (mode) */

        #     0x0000 Read only
        #     0x0001 Write only
        #     0x0002 Read/Write
        #     0x0200 Create

        log.debug("#######################")
        log.debug("yaffs open ")
        log.debug("#######################")
        drv = qemu.get_arg(0)
        drive = IosDev.get_driver(drv)
        name = qemu.read_string(qemu.get_arg(1))
        name = drive + name
        flags = qemu.get_arg(2)
        mode = qemu.get_arg(3)
        log.debug("drv: %s, drive: %s, name: %s, flags: %s, mode: %s\n", drv, drive, name, flags, mode)
        return self.model.creat_or_open(name,flags,mode)

    @bp_handler(['close'])
    def close(self, qemu, bp_addr):
        '''
            close break point handler
        '''
        fd = qemu.get_arg(0)
        log.debug("#######################")
        log.debug("yaffs close")
        log.debug("#######################")
        log.debug("\tFD: %d" % fd)
        try:
            log.debug("CLOSING FILE: %s" % self.model.fd_table[fd])
            self.model.close( fd )
            return True, 0
        except OSError as err:
            print(err)
            log.debug("EXCEPTION CAUGHT RETURNING ERROR")
            return True,  0xffffffff

    @bp_handler(['read'])
    def read(self, qemu, bp_addr):
        '''
            read break point handler
        '''
        log.debug("#######################")
        log.debug("yaffs read ")
        log.debug("#######################")
        log.debug("\tFD: %d" % qemu.get_arg(0))
        fd = qemu.get_arg(0)
        buf_ptr = qemu.get_arg(1)
        size = qemu.get_arg(2)
        data = self.model.read(fd, size)
        log.debug(data[:256])
        if len(data):
            for i, dat in enumerate(data):
                qemu.write_memory(buf_ptr + i, 1, dat)
        return True, len(data)

    @bp_handler(['write'])
    def write(self, qemu, bp_addr):
        '''
            write break point handler
        '''
        log.debug("#######################")
        log.debug("yaffs write ")
        log.debug("#######################")
        fd = qemu.get_arg(0)
        buf_ptr = qemu.get_arg(1)
        size = qemu.get_arg(2)
        buf = qemu.read_memory(buf_ptr, 1, size, raw=True)
        self.model.write(fd, buf)
        return True, len(buf)

    @bp_handler(['ioctl'])
    def ioctl(self, qemu, bp_addr):
        '''
            ioctl bp_handler
        '''
        log.debug("#######################")
        log.debug("yaffs ioctl")
        log.debug("#######################")
        fd = qemu.get_arg(0)
        func = qemu.get_arg(1)
        arg = qemu.get_arg(2)

        if func in self.switcher.keys():
            if isinstance(self.switcher[func], types.FunctionType):
                #return True,self.switcher[func](self,qemu, bp_addr)
                return self.switcher[func](self,qemu, bp_addr,fd,arg)
            log.debug("Unimplemented Function, ioctl # %i : %s", func, self.switcher[func])
            log.debug("fd: %d, func: %d, arg: %#x :", fd, func, arg)
            log.debug(qemu.read_memory(arg, 1, 4))
            return False, None
        log.debug("Undefined Function %s", func)
        input("Press Enter to continue")
        return True, 0

    # Taken from https://github.com/WangDongfang/DfewOS/blob/master/ios/lib/ioLib.h
    switcher = {
    # /* ioctl function codes */
        1: fio_read, #0x1 #FIONREAD /* get num chars available to read */
        2: "FIOFLUSH", #0x2 # /* flush any chars in buffers */
        3: "FIOOPTIONS", #0x3 #/* set options (FIOSETOPTIONS) */
        4: "FIOBAUDRATE", #0x4 /* set serial baud rate */
        5: "FIODISKFORMAT", #0x5 /* format disk */
        6: "FIODISKINIT", #0x6 # /* initialize disk directory */
        7: fio_seek, #0x7 #FIOSEEK /* set current file char position */
        8: fio_where, #0x8 #FIOWHERE /* get current file char position */
        9: "FIODIRENTRY", #0x9 /* return a directory entry (obsolete)*/
        10: "FIORENAME", #0xa # /* rename a directory entry */
        11: "FIOREADYCHANGE", #0xb /* return TRUE if there has been a media change on the device */
        12: "UNKNOWN", #0xc /* get num chars still to be written */
        13: "FIODISKCHANGE", #0xd /* set a media change on the device */
        14: "FIOCANCEL", #0xe /* cancel read or write on the device */
        15: "FIOSQUEEZE", #0xf # /* OBSOLETED since RT11 is obsoleted */
        16: "FIONBIO", #0x10 /* set non-blocking I/O; SOCKETS ONLY!*/
        17: "FIONMSGS", #0x11 /* return num msgs in pipe */
        18: "FIOGETNAME", #0x12 /* return file name in arg */
        19: "FIOGETOPTIONS", #0x13 /* get options */
        20: "FIOISATTY", #0x14 /* is a tty */
        21: "FIOSYNC", #0x15 #/* sync to disk */
        22: "FIOPROTOHOOK", #0x16 /* specify protocol hook routine */
        23: "FIOPROTOARG", #0x17 /* specify protocol argument */
        24: "FIORBUFSET", #0x18 /* alter the size of read buffer  */
        25: "FIOWBUFSET", #0x19 /* alter the size of write buffer */
        26: "FIORFLUSH", #0x1a /* flush any chars in read buffers */
        27: "FIOWFLUSH", #0x1b /* flush any chars in write buffers */
        28: "FIOSELECT", #0x1c /* wake up process in select on I/O */
        29: "FIOUNSELECT", #0x1d /* wake up process in select on I/O */
        30: "FIONFREE", #0x1e # /* get free byte count on device */
        31: "FIOMKDIR", #0x1f # /* create a directory */
        32: "FIORMDIR", #0x20 # /* remove a directory */
        33: "FIOLABELGET", #0x21 # /* get volume label */
        34: "FIOLABELSET", #0x22 # /* set volume label */
        35: "FIOATTRIBSET", #0x23 #/* set file attribute */
        36: "FIOCONTIG", #0x24 #/* allocate contiguous space */
        37: fio_read_dir, #0x25 #FIOREADDIR /* read a directory entry (POSIX) */
        38: fio_fstat_get, #0x26 #FIOFSTATGET /* get file status info - legacy */
        39: "FIOUNMOUNT", #0x27 # /* unmount disk volume */
        40: "FIOSCSICOMMAND", #0x28 /* issue a SCSI command */
        41: "FIONCONTIG", #0x29 # /* get size of max contig area on dev */
        42: "FIOTRUNC", #0x2a # /* truncate file to specified length */
        43: "FIOGETFL", #0x2b # /* get file mode, like fcntl(F_GETFL) */
        44: fio_time_set, #0x2c #FIOTIMESET /* change times on a file for utime() */
        45: "FIOINODETONAME", #0x2d # /* given inode number, return filename*/
        46: "FIOFSTATFSGET", #0x2e # /* get file system status info */
        47: "fio_move", #0x2f #FIOMOVE /* move file, ala mv, (mv not rename) */
    #/* 64-bit ioctl codes, "long long *" expected as 3rd argument */
        48: "UNKNOWN", #0x30
        49: "FIOCONTIG64", #0x31 #
        50: "FIONCONTIG64", #0x32 #
        51: "FIONFREE64", #0x33 #
        52: "FIONREAD64", #0x34 #
        53: "FIOSEEK64", #0x35 #
        54: "FIOWHERE64", #0x36 #
        55: "FIOTRUNC64", #0x37 #
        56: "FIOCOMMITFS", #0x38
        57: "FIODATASYNC", #0x39 /* sync to I/O data integrity */
        58: "FIOLINK", #0x3a /* create a link */
        59: "FIOUNLINK", #0x3b /* remove a link */
        60: "FIOACCESS", #0x3c /* support POSIX access() */
        61: "FIOPATHCONF", #0x3d /* support POSIX pathconf() */
        62: "FIOFCNTL", #0x3e /* support POSIX fcntl() */
        63: "FIOCHMOD", #0x3f /* support POSIX chmod() */
        64: fio_fstat_get, #"FIOFSTATGET", #0x40 /* get stat info - POSIX  */
        65: "FIOUPDATE", #0x41 /* update dosfs default create option */
    # /* These are for HRFS but can be used for other future file systems */
        66: "FIOCOMMITPOLICYGETFS", #0x42 /* get the file system commit policy */
        67: "FIOCOMMITPOLICYSETFS", #0x43 /* set the file system commit policy */
        68: "FIOCOMMITPERIODGETFS", #0x44 /* get the file system's periodic  commit interval in milliseconds */
        69: "FIOCOMMITPERIODSETFS", #0x45 /* set the file system's periodic  */
        70: "FIOFSTATFSGET64", #0x46 /* get file system status info commit interval in milliseconds */
    }

