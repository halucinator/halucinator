# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
'''dos_fs_model'''

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def translate_flags( flags):
    '''VxWorks defines their own flag values,
    so we need to translate them to POSIX'''
    bit_flags = {0x0001: os.O_WRONLY, 0x0002: os.O_RDWR, 0x0200: os.O_CREAT}
    new_flags = 0x00
    for b in bit_flags:
        if b & flags:
            new_flags |= bit_flags[b]
    return new_flags


def is_mkdir( mode):
    '''The VxWorks flag FSTAT_DIR indicates to create a directory'''
    return mode & 0x4000


class DosFsModel(object):
    '''This is the DOS FS model used for by the dos_fs bp_handlers.'''
    readdir = {}
    localDir = "tmp/HALucinator/FS" #TODO: make configurable
    fd_table ={}
    st_dev = 2
    st_mode = 2
    st_nlink = 1
    st_size = 8
    st_atime_64 = 8
    st_atime_32 = 4
    st_mtime_64 = 8
    st_mtime_32 = 4
    st_blksize = 8
    st_blocks = 8
    st_attrib = 1

    @classmethod
    def get_filename(cls, fd):
        ''' Returns full path of fd '''
        if fd in cls.fd_table:
            return cls.fd_table[fd]
        return None

    @classmethod
    def read(cls,fd, size):
        '''read'''
        return os.read(fd, size)

    @classmethod
    def write(cls,fd, buf):
        '''write'''
        os.write(fd, buf)


    @classmethod
    def close(cls,fd):
        '''close'''
        os.close( fd )


    @classmethod
    def creat_or_open(cls, name, flags, mode):
        '''
           Opens a file, or creates it of not present
           name: Filename
           flags:
           mode: (Open Mode)

           returns: fd on success, or -1 on failure
        '''
        if (len(name) > 1) and name.strip():
            fn = os.path.abspath(cls.localDir + name)
            parent_path = fn.rsplit('/', 1)[0]
            Path(parent_path).mkdir(parents=True, exist_ok=True)
            d = os.path.dirname(fn)
            if os.path.exists(d):
                fl = translate_flags(flags)
                log.debug("vxworks flags: %s, translated flags: %s", flags, fl)
                if is_mkdir(mode):
                    if os.path.exists(fn):
                        fd = os.open(fn, os.O_RDONLY)
                        cls.fd_table[fd] = fn
                        log.debug("FD RET: %s", fd)
                        return True, fd
                    log.debug("CREATING DIR: %s", fn)
                    os.mkdir(fn)
                    fd = os.open(fn, os.O_RDONLY)
                    cls.fd_table[fd] = fn
                    log.debug("FD RET: %s", fd)
                    return True, fd
                try:
                    fd = os.open(fn, fl)
                    log.debug("OPENING FILE: %s", fn)
                    cls.fd_table[fd] = fn
                    log.debug("FD RET: %s", fd)

                    return True, fd
                except OSError as e:
                    log.debug("EXCEPTION CAUGHT RETURNING ERROR")
                    return True,  0xffffffff
            log.debug("PATH DOES NOT EXIST: %s", d)
            return True, 0xffffffff
        return True, 0xffffffff

    @classmethod
    def delete(cls,drv,path,drive):
        '''delete'''
        if drive is None:
            log.debug("NO DRIVER INITALIZED")
            return 0

        log.debug(cls.localDir + drive + path )
        if os.path.exists(cls.localDir + drive + path ):
            if os.path.isdir(cls.localDir + drive + path ):
                try:
                    os.rmdir(cls.localDir + drive + path)
                    return  0
                except OSError as error:
                    log.error(error)
                    return 0xffffffff

            os.remove(cls.localDir + drive + path )
            return 0

        log.error("The file does not exist")
        return 0xffffffff

    @classmethod
    def fio_move(cls,fd, new_path):
        '''fio_move'''
        os.rename(cls.fd_table[fd], cls.localDir + new_path)

    @classmethod
    def fio_time_set(cls,fd, atime, modtime):
        '''fio_time_set'''
        os.utime(cls.fd_table[fd], (atime, modtime))

    @classmethod
    def fio_read(cls,fd):
        '''fio_read'''
        cur = os.lseek(fd, 0,os.SEEK_CUR)
        end = os.stat(fd).st_size - 1
        rem = end - cur
        return rem

    @classmethod
    def fio_seek(cls,fd, arg):
        '''fio_seek'''
        os.lseek(fd, arg, os.SEEK_CUR)

    @classmethod
    def fio_where(cls,fd):
        '''fio_where'''
        return os.lseek(fd, 0,  os.SEEK_CUR)

    @classmethod
    def fio_read_dir(cls, fd, init):
        '''fio_read_dir'''
        if init:
            cls.readdir[fd] = os.listdir(cls.fd_table[fd])
            if len(cls.readdir[fd])==0:
                del cls.readdir[fd]
                return None
            ret_val = cls.readdir[fd][0]
            cls.readdir[fd].remove(ret_val)
            ret_val = ret_val.encode('utf-8')
            return ret_val

        if len(cls.readdir[fd])>0:
            ret_val = cls.readdir[fd][0]
            cls.readdir[fd].remove(ret_val)
            ret_val = ret_val.encode('utf-8')
            return ret_val

        # write null to retun value name
        del cls.readdir[fd]
        return None

    @classmethod
    def fio_fstat_get(cls,fd):
        '''fio_fstat_get'''
        #TODO:   Do value checking size checking to make sure
        # we don't try to smash incorrect sizes
        fstat = os.fstat(fd)
        fsfstat = os.fstatvfs(fd)
        ret = {
            "st_dev": fstat.st_dev,
            "st_nlink" : fstat.st_nlink,
            "st_size" : fstat.st_size,
            "st_blksize" : fsfstat.f_bsize,
            "st_blocks" : fsfstat.f_blocks,
            # TODO: Perhaps implement a shadow permission system so VxWorks see
            # proper permissions, but we have direct access to them from host
            # would need to be non-volatile
            "st_attrib" : 0, # Maybe a better set to over permission
            "st_mode" : fstat.st_mode,
            "st_atime" : int(fstat.st_atime),
            "st_mtime" : int(fstat.st_mtime)
        }
        return ret

    @classmethod
    def fio_rename(cls,fd,new_name):
        '''fio_rename'''
        oldname = cls.fd_table[fd]
        lst = oldname.split("/")
        lst.pop()
        new_name_with_path = '/'.join(map(str, lst))
        new_name_with_path += '/' + new_name
        os.rename(oldname, new_name_with_path)
        cls.fd_table[fd] = new_name_with_path
