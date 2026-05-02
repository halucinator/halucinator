# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.


"""ios dev module"""
import logging
import os

from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.utty import UTTYModel

log = logging.getLogger(__name__)


class IosDev(BPHandler):
    """ios dev class bp handler"""

    drivers = {}
    localDir = "tmp/HALucinator/FS"
    models = [UTTYModel]

    @classmethod
    def get_driver(cls, drv):
        """get_driver"""
        if drv in cls.drivers:
            return cls.drivers[drv]
        return None

    @bp_handler(["iosDevAdd"])
    def ios_dev_add(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_dev_add"""
        log.debug("ios_dev_add")
        name = qemu.read_string(qemu.get_arg(1))
        log.debug("\tDEV_HDR:  0x%08x", qemu.get_arg(0))
        log.debug("\tName:     %s", name)
        log.debug("\tDriver:   %s", qemu.get_arg(2))
        prev_dir = ""
        for sub_dir in name.split("/"):
            _dir = os.path.abspath(self.localDir + "/" + prev_dir + sub_dir)
            if not os.path.exists(_dir):
                log.debug(self.drivers)
                os.mkdir(_dir)
            prev_dir = prev_dir + "/" + sub_dir
        self.drivers[qemu.get_arg(0)] = name
        for model in self.models:
            model.attach_interface(name)
        return False, None

    @bp_handler(["iosCreate"])
    def ios_create(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_create"""
        log.debug("ios_create")
        name = qemu.read_string(qemu.get_arg(1))
        log.debug("\tDEV_HDR:  %s", qemu.get_arg(0))
        log.debug("\tPTR:      0x%08x", qemu.get_arg(1))
        log.debug("\tName:     %s", name)
        log.debug("\tMode:     %s", qemu.get_arg(2))
        return False, None
        # read_string, 0

    @bp_handler(["iosDelete"])
    def ios_delete(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_delete"""
        log.debug("ios_delete")

        name = qemu.read_string(qemu.get_arg(1))
        log.debug("\tDEV_HDR:  %s", qemu.get_arg(0))
        log.debug("\tPTR:      0x%08x", qemu.get_arg(1))
        log.debug("\tName:     %s", name)
        return False, None
        # return True, 0

    @bp_handler(["iosOpen"])
    def ios_open(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_open"""
        log.debug("ios_open")

        name = qemu.read_string(qemu.get_arg(1))
        flags = qemu.get_arg(2)
        mode = qemu.get_arg(3)
        log.debug("\tDEV_HDR:  %s", hex(qemu.get_arg(0)))
        log.debug("\tPTR:      0x%08x", qemu.get_arg(1))
        log.debug("\tName:     %s", name)
        log.debug("\tFlags:    0x%04x", flags)
        log.debug("\tMode:     0x%04x", mode)

        return False, None

    @bp_handler(["iosClose"])
    def ios_close(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_close"""
        log.debug("ios_close")
        fd = qemu.get_arg(0)
        log.debug("\tFD:           0x%08x", fd)
        return False, None

    @bp_handler(["iosRead"])
    def ios_read(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_read"""
        log.debug("ios_read")
        fd = qemu.get_arg(0)
        buf = qemu.get_arg(1)
        size = qemu.get_arg(2)
        log.debug("\tFD:           0x%08x", fd)
        log.debug("\tBUFFER:       0x%08x", buf)
        log.debug("\tMAX_BYTES:    %d", size)
        return False, None

    @bp_handler(["iosWrite"])
    def ios_write(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_write"""
        log.debug("ios_write")
        log.debug("\tFD:           0x%08x", qemu.get_arg(0))
        log.debug("\tBUFFER:       0x%08x", qemu.get_arg(1))
        log.debug("\tMAX_BYTES:    %d", qemu.get_arg(2))
        log.debug("STRING IN BUFFER %s", qemu.read_string(qemu.get_arg(1)))

        return False, None

    @bp_handler(["iosIoctl"])
    def ios_ioctl(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_ioctl"""
        log.debug("ios_ioctl")
        log.debug("\tFD:       0x%08x", qemu.get_arg(0))
        log.debug("\tFN:       0x%08x", qemu.get_arg(1))
        log.debug("\tARG:      0x%08x", qemu.get_arg(2))
        return False, None

    @bp_handler(["iosFdNew"])
    def ios_fd_new(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_fd_new"""
        log.debug("ios_fd_new")
        hdr = qemu.get_arg(0)
        name = qemu.read_string(qemu.get_arg(1))
        log.debug("\tDEV_HDR:  %s", hdr)
        log.debug("\tPTR:      0x%08x", qemu.get_arg(1))
        log.debug("\tName:     %s", name)
        return False, None

    @bp_handler(["iosFdFree"])
    def ios_fd_free(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_fd_free"""
        log.debug("ios_fd_free")
        fd = qemu.get_arg(0)
        log.debug("\tFD:       0x%08x", fd)
        return False, None

    @bp_handler(["iosFdSet"])
    def ios_fd_set(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """ios_fd_set"""
        log.debug("ios_fd_set")
        name = qemu.read_string(qemu.get_arg(2))
        log.debug("\tFD:  %s", qemu.get_arg(0))
        log.debug("\tDEV_HDR:  %s", qemu.get_arg(1))
        log.debug("\tName:     %s", name)
        log.debug("\tDRV_FD:      0x%08x", qemu.get_arg(3))
        return False, None

    @bp_handler(["iosDevFind"])
    def ios_dev_find(
        self, qemu, bp_addr
    ):  # pylint: disable=no-self-use,unused-argument
        """ios_dev_find"""
        log.debug("ios_dev_find")
        name = qemu.read_string(qemu.get_arg(0))
        log.debug("\tName:     %s", name)
        return False, None

    DRV_INSTALL_NUM_ARGS = 7

    @bp_handler(["iosDrvInstall"])
    def ios_drv_install(
        self, qemu, bp_addr
    ):  # pylint: disable=no-self-use,unused-argument
        """ios_drv_install"""

        # initalize fd table
        with open("drivers.txt", "a") as outfile:
            outfile.write("#######################\n")
            outfile.write(" Drivers\n")
            for arg_num in range(IosDev.DRV_INSTALL_NUM_ARGS):
                outfile.write(f" {hex(qemu.get_arg(arg_num))}\n")
            outfile.write("#######################\n")

        log.debug("#######################")
        log.debug(" Drivers")
        for arg_num in range(IosDev.DRV_INSTALL_NUM_ARGS):
            log.debug(" %s", (hex(qemu.get_arg(arg_num))))
        log.debug("#######################")
        return False, None

    @bp_handler(["ios_error"])
    def ios_error(self, qemu, handler):  # pylint: disable=no-self-use,unused-argument
        """ios_error"""
        log.info("IOS ERROR")
        log.debug('IOS ERROR\nEnter "exit" to continue')
        # import IPython; IPython.embed()
        return False, None
