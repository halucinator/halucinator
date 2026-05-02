# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

'''tyDev module'''
import logging
import types

from halucinator.bp_handlers.vxworks.ios_dev import IosDev
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler
from halucinator.peripheral_models.utty import UTTYModel

log = logging.getLogger(__name__)


class TYIsrState:
    """holds the ty isr state"""

    # pylint: disable=too-few-public-methods
    def __init__(self, tty_dev_struct, dev_id, read_limit):
        self.tty_dev_struct = tty_dev_struct
        self.dev_id = dev_id
        self.read_limit = read_limit


class TYDev(BPHandler):
    """
    TYDev
    Provides intercepts for ttyDev devices

    Usage:

    class: halucinator.bp_handlers.vxworks.ty_dev.TYDev
    class_args: {interfaces: {<BOARD_SPECIFIC>(e.g. /tyCo/0): {irq_num: <BOARD_SPECIFIC>(e.g. 0x2),
                                                               enabled: true},
                          <BOARD_SPECIFIC>(e.g. /tyCo/1): {irq_num: <BOARD_SPECIFIC>(e.g. 0x3),
                                                           enabled: true},
             ird: tyIRd,
             use_rx_task: false
             tty_dev_offset:   0x10  (offest to the into the driver structure for ttyDev device)
             sema_ptr_offset:  0x650 (The offset into the driver stucture for the RXtask semaphore)}
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        model=UTTYModel,
        tty_dev_offset=0x10,
        sema_ptr_offset=0x650,
        interfaces=None,
        ird="tyIRd",
        use_rx_task=False,
    ):  # pylint: disable=too-many-arguments,too-many-instance-attributes
        super().__init__()

        self.tty_dev_offset = tty_dev_offset
        self.sema_ptr_offset = sema_ptr_offset
        self.utty_model = model

        self.ird = ird
        self.use_rx_task = use_rx_task
        self.state_stack = []
        self.done_stack = []
        self.ioctl_options = 0

        if interfaces is not None:
            for name, items in interfaces.items():
                self.utty_model.add_interface(name, **items)

    def get_utty_id(self, qemu, p_ty_dev):  # pylint: disable=no-self-use
        """get_utty_id"""
        # Offset may change per device make class arg
        offset = 0xC
        p_dev_hdr = int.from_bytes(
            qemu.read_memory((p_ty_dev + offset), 1, 4, raw=True), "little"
        )
        driver = IosDev.get_driver(p_dev_hdr)

        if driver is None:
            driver = IosDev.get_driver(p_ty_dev)
            if driver is None:
                log.error("p_ty_dev %s  p_dev_hdr %s Drivers", p_ty_dev, p_dev_hdr)
                log.error(IosDev.drivers)
                raise Exception("Error driver not found! Probably need to intercept iosDevAdd with"
                                " ios_dev.IosDev.iosDevAdd")
        return driver

    @bp_handler(["tyISR"])
    def ty_isr(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """ty_isr"""
        log.debug("ty_isr")
        p_ty_dev = qemu.get_arg(0)
        dev_id = self.get_utty_id(qemu, p_ty_dev)
        sema_ptr = p_ty_dev + self.sema_ptr_offset
        sema_val = qemu.read_memory(sema_ptr, 4, 1)

        # Get the Number of chars on the buffer to ensure data is available and set the max number
        # of chars we want to read in this interrupt
        num_chars_rx = self.utty_model.get_rx_buff_size(dev_id)

        # This structure is needed for the underlying ird functions that are called
        tty_dev_struct = qemu.read_memory((p_ty_dev + self.tty_dev_offset), 4, 1)

        if sema_val == 0 or not self.use_rx_task:
            # Generate a state for the current device
            isr_state = TYIsrState(tty_dev_struct, dev_id, num_chars_rx)

            if num_chars_rx > 0:
                char = self.utty_model.get_rx_char(dev_id)
                isr_state.read_limit = isr_state.read_limit - 1
                if isr_state.read_limit == 0:
                    return qemu.call(
                        self.ird, [tty_dev_struct, char], self, "receive_done"
                    )
                # Add State to stack to ensure the correct state is retreived when
                # isr_execute_read is called
                self.state_stack.append(isr_state)
                return qemu.call(
                    self.ird, [tty_dev_struct, char], self, "isr_execute_read"
                )

        # Just release the semaphore so the RxTask will run which will
        # actually read the data
        log.debug("Giving Sem to task")
        qemu.irq_disable_bp(self.utty_model.interfaces[dev_id].irq_num)
        return qemu.call("semGive", [sema_val], self, "receive_done")

    @bp_handler(["isr_execute_read"])
    def isr_execute_read(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """isr_execute_read"""
        log.debug("isr_execute_read")
        # Get the last device's state that added to the state stack(last device that interrupted)
        isr_state = self.state_stack.pop()
        if isr_state.read_limit > 1:
            char = self.utty_model.get_rx_char(isr_state.dev_id)
            isr_state.read_limit = isr_state.read_limit - 1
            self.state_stack.append(isr_state)
            return qemu.call(
                self.ird, [isr_state.tty_dev_struct, char], self, "isr_execute_read"
            )
        char = self.utty_model.get_rx_char(isr_state.dev_id)
        isr_state.read_limit = 0

        return qemu.call(
            self.ird, [isr_state.tty_dev_struct, char], self, "receive_done"
        )

    @bp_handler(["receive_done"])
    def receive_done(
        self, qemu, bp_addr
    ):  # pylint: disable=unused-argument, no-self-use
        """receive_done"""
        log.debug(
            "DONE With Receive ISR ...................................................."
        )
        return True, None

    @bp_handler(["task_receive_done"])
    def task_receive_done(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """receive_done"""
        log.debug(
            "DONE With Task Receive ...................................................."
        )
        done_state = self.done_stack.pop()
        irq_num = self.utty_model.interfaces[done_state.dev_id].irq_num
        qemu.irq_enable_bp(irq_num)
        return True, None

    @bp_handler(["tyITx", "utyITx"])
    def ty_it_x(self, qemu, bp_addr):  # pylint: disable=unused-argument, no-self-use
        """ty_it_x"""
        log.debug("tyITx")
        return False, None

    @bp_handler(["utyIRd", "tyIRd"])
    def ty_ir_d(self, qemu, bp_addr):  # pylint: disable=unused-argument, no-self-use
        """Tis should differ based on rx or tx interrupt"""
        log.debug("tyIRd")
        log.debug("pDEV: %s", hex(qemu.get_arg(0)))
        return False, None

    @bp_handler(["utyRead", "tyRead"])
    def ty_read(self, qemu, bp_addr):  # pylint: disable=unused-argument, no-self-use
        """This should differ based on rx or tx interrupt"""
        log.debug("tyRead")
        log.debug("pDEV: %s", hex(qemu.get_arg(0)))
        return False, None

    @bp_handler(["rxTask"])
    def rx_task(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """
        RX_task:
        This is opperation is used for devices that read in serial data at the
        task level. This is trigger using a semaphore in the tyISR routine.
        If using will need to set sema_ptr_offset appropriately
        """
        p_ty_dev = qemu.get_arg(0)
        dev_id = self.get_utty_id(qemu, p_ty_dev)
        num_chars_rx = self.utty_model.get_rx_buff_size(dev_id)

        tty_dev_struct = qemu.read_memory((qemu.get_arg(0) + 0x10), 4, 1)
        isr_state = TYIsrState(tty_dev_struct, dev_id, num_chars_rx)

        if num_chars_rx > 0:
            char = self.utty_model.get_rx_char(dev_id)
            isr_state.read_limit = isr_state.read_limit - 1

            if isr_state.read_limit == 0:
                self.done_stack.append(isr_state)
                return qemu.call(
                    self.ird, [tty_dev_struct, char], self, "task_receive_done"
                )
            self.state_stack.append(isr_state)
            return qemu.call(
                self.ird, [tty_dev_struct, char], self, "task_execute_read"
            )

        log.debug(
            " ........................NO DATA TO PROCESS............................"
        )

        return True, None

    @bp_handler(["task_execute_read"])
    def execute_read(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """execute_read"""
        isr_state = self.state_stack.pop()
        if isr_state.read_limit > 1:
            char = self.utty_model.get_rx_char(isr_state.dev_id)
            isr_state.read_limit = isr_state.read_limit - 1
            self.state_stack.append(isr_state)
            return qemu.call(
                self.ird, [isr_state.tty_dev_struct, char], self, "task_execute_read"
            )

        char = self.utty_model.get_rx_char(isr_state.dev_id)
        return qemu.call(
            self.ird, [isr_state.tty_dev_struct, char], self, "receive_done"
        )

    @bp_handler(["utyWrite", "tyWrite"])
    def ty_write(self, qemu, bp_addr):  # pylint: disable=unused-argument
        """ty_write"""
        log.debug("In tyWrite")
        p_ty_dev = qemu.get_arg(0)
        dev_id = self.get_utty_id(qemu, p_ty_dev)
        buf_ptr = qemu.get_arg(1)
        buf_size = qemu.get_arg(2)
        buf = qemu.read_memory(buf_ptr, 1, buf_size, raw=True)
        try:
            log.debug("In tyWrite sending: %s to %s", buf.decode("ascii"), dev_id)
        except UnicodeError:
            log.debug("In tyWrite sending non-ascii bytes to %s", dev_id)

        self.utty_model.tx_buf(dev_id, buf)
        return True, buf_size

    def fio_n_read(self, qemu, p_obj, arg):
        """fio_n_read"""
        p_ty_dev = p_obj
        dev_id = self.get_utty_id(qemu, p_ty_dev)
        buf_size = self.utty_model.get_rx_buff_size(dev_id)
        log.debug("FIONREAD %s bytes", buf_size)
        qemu.write_memory(arg, 2, buf_size)
        return True, 0

    def fio_rflush(self, qemu, p_obj, arg):  # pylint: disable=unused-argument
        """fio_rflush"""
        log.debug("FIORFLUSH")
        p_ty_dev = p_obj
        dev_id = self.get_utty_id(qemu, p_ty_dev)
        self.utty_model.flush(dev_id)
        return True, 0

    def fio_setoptions(self, qemu, p_obj, arg):  # pylint: disable=unused-argument
        """
        Implement Set Options
        """
        self.ioctl_options = arg
        return True, 0

    def fio_getoptions(self, qemu, p_obj, arg):  # pylint: disable=unused-argument
        """
        Implement Get Options
        """
        qemu.write_memory(arg, 4, self.ioctl_options)
        return True, 0

    @bp_handler(["utyIoctl", "tyIoctl"])
    def ty_ioctl(self, qemu, addr):  # pylint: disable=unused-argument
        """
        tyIoctl bp_handler
        """
        #  SIO_BAUD_SET          0x1003
        #  SIO_BAUD_GET          0x1004
        #  SIO_HW_OPTS_SET       0x1005
        #  SIO_HW_OPTS_GET       0x1006
        #  SIO_MODE_SET          0x1007
        #  SIO_MODE_GET          0x1008
        #  SIO_AVAIL_MODES_GET   0x1009

        p_obj = qemu.get_arg(0)
        func = qemu.get_arg(1)
        arg = qemu.get_arg(2)
        if func in TYDev.switcher:
            if isinstance(TYDev.switcher[func], types.FunctionType):
                # return True,self.switcher[func](self,qemu, bp_addr)
                return TYDev.switcher[func](self, qemu, p_obj, arg)
            log.debug(
                "(tyIoctl) Unimplemented Serial IOCTL: %s(%i), arg 0x%08x, lr: 0x%08x",
                TYDev.switcher[func],
                func,
                arg,
                qemu.regs.lr,
            )
            return False, None

        log.debug("(utyIoctl) Undefined Serial IOCTL %s", hex(func))
        return False, None

    # Lookup table for ioctl handlers
    # from https://github.com/400plus/400plus/blob/master/vxworks/ioLib.h
    switcher = {
        1: "fio_n_read",
        2: "FIOFLUSH",
        3: fio_setoptions,
        4: "FIOBAUDRATE",
        5: "FIODISKFORMAT",
        6: "FIODISKINIT",
        7: "FIOSEEK",
        8: "FIOWHERE",
        9: "FIODIRENTRY",
        10: "fio_rename",
        11: "fio_readYCHANGE",
        12: "FIONWRITE",
        13: "FIODISKCHANGE",
        14: "FIOCANCEL",
        15: "FIOSQUEEZE",
        16: "FIONBIO",
        17: "FIONMSGS",
        18: "FIOGETNAME",
        19: fio_getoptions,
        20: "FIOISATTY",
        21: "FIOSYNC",
        22: "FIOPROTOHOOK",
        23: "FIOPROTOARG",
        24: "FIORBUFSET",
        25: "FIOWBUFSET",
        26: fio_rflush,
        27: "FIOWFLUSH",
        28: "FIOSELECT",
        29: "FIOUNSELECT",
        30: "FIONFREE",
        31: "FIOMKDIR",
        32: "FIORMDIR",
        33: "FIOLABELGET",
        34: "FIOLABELSET",
        35: "fio_attrib_set",
        36: "FIOCONTIG",
        37: "fio_readDIR",
        38: "FIOFSTATGET",
        39: "FIOUNMOUNT",
        40: "FIOSCSICOMMAND",
        41: "FIONCONTIG",
        42: "FIOTRUNC",
        43: "FIOGETFL",
        44: "fio_time_set",
        45: "FIOINODETONAME",
        46: "FIOFSTATFSGET",
        47: "fio_move",  # 0x2f #FIOMOVE
        # /* 64-bit ioctl codes, "long long *" expected as 3rd argument */
        48: "UNKNOWN",  # 0x30
        49: "FIOCONTIG64",  # 0x31 #
        50: "FIONCONTIG64",  # 0x32 #
        51: "FIONFREE64",  # 0x33 #
        52: "FIONREAD64",  # 0x34 #
        53: "FIOSEEK64",  # 0x35 #
        54: "FIOWHERE64",  # 0x36 #
        55: "FIOTRUNC64",  # 0x37 #
        56: "FIOCOMMITFS",  # 0x38
        57: "FIODATASYNC",  # 0x39
        58: "FIOLINK",  # 0x3a
        59: "FIOUNLINK",  # 0x3b
        60: "FIOACCESS",  # 0x3c
        61: "FIOPATHCONF",  # 0x3d
        62: "FIOFCNTL",  # 0x3e
        63: "FIOCHMOD",  # 0x3f
        64: "FIOFSTATGET",  # 0x40
        65: "FIOUPDATE",  # 0x41
        # /* These are for HRFS but can be used for other future file systems */
        66: "FIOCOMMITPOLICYGETFS",  # 0x42
        67: "FIOCOMMITPOLICYSETFS",  # 0x43
        68: "FIOCOMMITPERIODGETFS",  # 0x44
        69: "FIOCOMMITPERIODSETFS",  # 0x45
        70: "FIOFSTATFSGET64",  # 0x46
    }

