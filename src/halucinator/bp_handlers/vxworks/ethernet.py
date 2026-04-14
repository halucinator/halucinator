# Copyright 2021 National Technology & Engineering Solutions of Sandia, LLC
# (NTESS). Under the terms of Contract DE-NA0003525 with NTESS,
# the U.S. Government retains certain rights in this software.

"""Module to support ethernet"""
from __future__ import annotations

import logging
import types
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type

from halucinator.bp_handlers.bp_handler import BPHandler, HandlerFunction, bp_handler
from halucinator.peripheral_models.ethernet import EthernetModel

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget

log = logging.getLogger(__name__)

# pylint: disable=fixme


class Ethernet(BPHandler):
    """
    Ethernet class for handling bp and interactions
    """

    # pylint: disable=too-many-arguments,too-many-instance-attributes,too-many-public-methods
    def __init__(self, model: Type[EthernetModel] = EthernetModel, interfaces: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.mac: bytes = b""
        self.p_dev: Optional[int] = None
        self.p_net_pool: Optional[int] = None
        self.cl_pool_id: Optional[int] = None

        self.eth_model: Type[EthernetModel] = model

        self.net_pool_offset: int = 0x2AC
        self.cl_pool_id_offset: int = 0x33C
        self.handle_end_int_rcv_addr: Optional[int] = None

        if interfaces is not None:
            for name, items in interfaces.items():
                self.eth_model.add_interface(name, **items)

    # pylint: disable=duplicate-code
    def register_handler(
        self,
        qemu: HALQemuTarget,
        addr: int,
        func_name: str,
        end_handle_rcv: Optional[str] = None,
        net_pool_offset: Optional[int] = None,
        cl_pool_id_offset: Optional[int] = None,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments
        """
        isr_num:  The ISR number to trigger on reception of an ethernet frame
        interfaces: a dictionary if {eth0: {irq_name: xx, irq_num: 0x19}, eth1: {irq_num: 0x19}}
        """

        self.net_pool_offset = (
            net_pool_offset if net_pool_offset is not None else self.net_pool_offset
        )
        self.cl_pool_id_offset = (
            cl_pool_id_offset
            if cl_pool_id_offset is not None
            else self.cl_pool_id_offset
        )

        if end_handle_rcv is not None:
            self.handle_end_int_rcv_addr = qemu.avatar.config.get_addr_for_symbol(
                end_handle_rcv
            )
            if self.handle_end_int_rcv_addr is None:
                raise ValueError(
                    f"Could not find address for end_handle_rcv {end_handle_rcv}"
                )

        return super().register_handler(qemu, addr, func_name)

    @bp_handler(["netPoolInit"])
    def net_pool_init(self, qemu: HALQemuTarget, addr: int) -> Tuple[bool, None]:  # pylint: disable=unused-argument,no-self-use
        """
        net_pool_init
        """
        with open("NetPools.txt", "a") as outfile:
            callee = qemu.avatar.config.get_symbol_name(qemu.regs.lr)
            outfile.write(f"Netpool: {hex(qemu.regs.r0)}, called from {callee}\n")
        return False, None

    def get_eth_id(self, qemu: HALQemuTarget) -> str:  # pylint: disable=unused-argument,no-self-use
        """
        get_eth_id
        """
        # TODO fix to support multiple interfaces
        return "eth0"  # self.eth_model.interfaces.keys()[0]

    def e_io_cs_addr(
        self, qemu: HALQemuTarget, p_obj: int, ptr_mac_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        EIOCGADDR
        """
        log.debug("e_io_cs_addr")
        self.mac = qemu.read_memory(ptr_mac_addr, 1, 10, raw=True)
        return True, 0

    def e_io_cg_addr(
        self, qemu: HALQemuTarget, p_obj: int, ptr_mac_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        EIOCSADDR
        """
        log.debug("e_io_cg_addr")
        qemu.write_memory(ptr_mac_addr, 1, self.mac, 6, raw=True)
        return True, 0

    def e_io_cs_flags(
        self, qemu: HALQemuTarget, p_obj: int, ptr_mac_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        EIOCSFLAGS
        """
        log.debug("e_io_cs_flags")
        self.mac = qemu.read_memory(ptr_mac_addr, 1, 10, raw=True)
        return True, 0

    # This is the IOCTL handler look up table
    switcher = {
        0x40046912: e_io_cs_addr,
        0x40046907: e_io_cg_addr,
        0x40046905: e_io_cs_flags,  # "EIOCSFLAGS",
        0x40046910: "EIOCGFLAGS",
        0x80046906: "EIOCPOLLSTART",
        0x80046904: "EIOCPOLLSTOP",
        0x8004690E: "EIOCGMIB2",
        0x8004690F: "EIOCGHDRLEN",
    }

    def get_packetdata(self, qemu: HALQemuTarget, mblk: int) -> Dict[str, Any]:  # pylint: disable=no-self-use,no-self-use
        """
        Reads the packet data from the mblk
        """
        mblk_out = {}
        mblk_out["addr"] = hex(mblk)
        mblk_out["mBlkHdr"] = {}

        mblk_out["mBlkPktHdr"] = qemu.read_memory((mblk + 0x1C), 4, 1)
        mblk_out["mBlkPktHdr_hex"] = hex(mblk_out["mBlkPktHdr"])
        mblk_out["mBlkHdr"]["m_data_hex"] = hex(mblk_out["mBlkHdr"]["m_data"])
        mblk_out["mBlkHdr"]["mNext"] = qemu.read_memory((mblk + 0x0), 4, 1)
        mblk_out["mBlkHdr"]["mNextPkt"] = qemu.read_memory((mblk + 0x4), 4, 1)
        mblk_out["mBlkHdr"]["m_data"] = qemu.read_memory((mblk + 0x8), 4, 1)
        mblk_out["mBlkHdr"]["m_len"] = qemu.read_memory((mblk + 0xC), 4, 1)
        mblk_out["mBlkHdr"]["mType"] = qemu.read_memory((mblk + 0x10), 1, 1)
        mblk_out["mBlkHdr"]["mflags"] = qemu.read_memory((mblk + 0x12), 1, 1)
        mblk_out["mBlkHdr"]["reserved"] = qemu.read_memory((mblk + 0x14), 2, 1)
        mblk_out["mBlkHdr"]["PKT_DATA"] = qemu.read_memory(
            mblk_out["mBlkHdr"]["m_data"], 1, mblk_out["mBlkHdr"]["m_len"], raw=True
        )
        mblk_out["p_cl_blk"] = qemu.read_memory(
            (mblk + 0x30),
            4,
            1,
        )
        mblk_out["p_cl_blk_hex"] = hex(mblk_out["p_cl_blk"])
        return mblk_out

    @bp_handler(["xSend"])
    def x_send(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        xSend break point handler
        """
        log.debug("x_send")
        mblk = self.get_packetdata(qemu, qemu.regs.r1)

        eth_id = self.get_eth_id(qemu)
        self.eth_model.tx_frame(eth_id, mblk["mBlkHdr"]["PKT_DATA"])
        log.debug("Sending MBlk: %s", mblk)
        return False, 0  # Is there a reason this is false?

    @bp_handler(["xUnload"])
    def x_unload(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument,no-self-use
        """
        xUnload bp_handler
        """
        log.debug("x_unload")
        input("Press Enter to continue")
        return False, 0

    @bp_handler(["xIoctl"])
    def x_ioctl(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        EIOCGADDR: get device address
        EIOCSADDR: set device address
        EIOCSFLAGS:set device flags
        EIOCGFLAGS: get device flags
            - IFF_ALLMULTI - This device receives all multicast packets.
            - IFF_BROADCAST - The broadcast address is valid.
            - IFF_DEBUG - Debugging is on.
            - IFF_LINK0 - A per link layer defined bit.
            - IFF_LINK1 - A per link layer defined bit.
            - IFF_LINK2 - A per link layer defined bit.
            - IFF_LOOPBACK - This is a loopback net.
            - IFF_MULTICAST - The device supports multicast.
            - IFF_NOARP - There is no address resolution protocol.
            - IFF_NOTRAILERS - The device must avoid using trailers.
            - IFF_OACTIVE - Transmission in progress.
            - IFF_POINTOPOINT - The interface is a point-to-point link.
            - IFF_PROMISC - This device receives all packets.
            - IFF_RUNNING - The device has successfully allocated needed resources.
            - IFF_SIMPLEX - The device cannot hear its own transmissions.
            - IFF_UP - The interface driver is up.
        EIOCPOLLSTART: Put device in polled mode
        EIOCPOLLSTOP:Put device in interrupt mode (exit polled mode).
        EIOCGMIB2: Get RFC 1213 MIB information from the driver. Call endM2Ioctl( ) to handle this.
        EIOCGHDRLEN: Get the size of the datalink header

        """
        p_obj = qemu.regs.r0
        func = qemu.regs.r1
        data = qemu.regs.r2

        if func in self.switcher:
            if isinstance(self.switcher[func], types.FunctionType):
                return self.switcher[func](self, qemu, p_obj, data)
            log.debug("(x_ioctl) Unimplemented Function: %s", self.switcher[func])
            return False, 0

        ret_addr = qemu.regs.lr
        sym = qemu.avatar.config.get_symbol_name(ret_addr)
        log.debug("(x_ioctl) Undefined Function %s called from %s ", hex(func), sym)
        return False, 0

    @bp_handler(["xPollSend"])
    def x_poll_send(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument, no-self-use
        """
        xPollSend bp_handler
        """
        log.debug("x_poll_send")
        input("Press Enter to continue")
        return False, 0

    @bp_handler(["xStart"])
    def x_start(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument
        """
        Xstart  bp_handler
        """
        log.debug("x_start")
        # TODO change model so doesn't start receiving until a start is called
        eth_id = self.get_eth_id(qemu)
        self.eth_model.flush(eth_id)
        self.eth_model.enable(eth_id)
        # self.eth_model.enable_rx_isr_bp(eth_id)
        return False, 0

    @bp_handler(["xLoad"])
    def x_load(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, int]:  # pylint: disable=unused-argument,no-self-use
        """
        xLoad bp_handler
        """
        log.debug("x_load")
        # input('Press Enter to continue')
        return False, 0

    @bp_handler(["xMCastAddrDel"])
    def x_m_cast_addr_del(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument,no-self-use
        """
        xMCastAddrDel bp_handler
        """
        log.debug("x_m_cast_addr_del")
        # input('Press Enter to continue')
        return False, 0

    @bp_handler(["xMCastAddrGet"])
    def x_m_cast_addr_get(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument,no-self-use
        """
        xMCastAddrGet bp_handler
        """
        log.debug("x_m_cast_addr_get")
        # input('Press Enter to continue')
        return False, 0

    @bp_handler(["xMCastAddrAdd"])
    def x_m_cast_addr_add(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> Tuple[bool, int]:  # pylint: disable=unused-argument,no-self-use
        """
        xMCastAddrAdd bp_handler
        """
        log.debug("x_m_cast_addr_add")
        # input('Press Enter to continue')
        return False, 0

    @bp_handler(["ethernet_isr"])
    def ethernet_isr(
        self, qemu: HALQemuTarget, bp_addr: int
    ) -> Any:  # pylint: disable=unused-argument,no-self-use
        """
        ethernet_isr bp_handler
        """
        log.debug("ethernet_isr")
        # eth_id = self.get_eth_id(qemu)
        # This will cause handleEndIntRcv below to execute
        return qemu.call(
            "netJobAdd", [self.handle_end_int_rcv_addr, qemu.get_arg(0), 0, 0, 0, 0]
        )

    @bp_handler(["handleEndIntRcv"])
    def handle_end_int_rcv(self, qemu: HALQemuTarget, bp_addr: int) -> Any:  # pylint: disable=unused-argument
        """handle_end_int_rcv"""
        self.p_dev = qemu.get_arg(0)
        self.p_net_pool = qemu.read_memory(self.p_dev + self.net_pool_offset, 4, 1)
        self.cl_pool_id = qemu.read_memory(self.p_dev + self.cl_pool_id_offset, 4, 1)
        # 0x5f =1520 (big enough for ethernet frame)
        return qemu.call(
            "netTupleGet", [self.p_net_pool, 0x5F0, 1, 1], self, "netTupleGet_return"
        )

    def set_mblk(self, qemu: HALQemuTarget, mblk_addr: int, data: bytes, flags: int = 3) -> None:
        """set_mblk"""
        # p_cl_blk = qemu.read_memory(
        #     (mblk_addr + 0x30),
        #     4,
        #     1,
        # )
        m_data = qemu.read_memory((mblk_addr + 0x8), 4, 1)
        qemu.write_memory(m_data, 1, data, len(data), raw=True)
        m_len = mblk_addr + 0xC
        qemu.write_memory(m_len, 4, len(data))
        qemu.write_memory(mblk_addr + 0x1C, 4, len(data))  # mBlkPktHdr
        # flags
        flags = qemu.read_memory(mblk_addr + 0x12, 2, 1)
        qemu.write_memory(mblk_addr + 0x12, 2, flags | 2)
        log.debug("Incoming Mblk: %s", self.get_packetdata(qemu, mblk_addr))

    @bp_handler(["netTupleGet_return"])
    def get_mblk(self, qemu: HALQemuTarget, bp_addr: int) -> Any:  # pylint: disable=unused-argument
        """get_mblk"""
        m_blk = qemu.regs.r0
        if m_blk == 0:
            raise TypeError("Failed to get netTuple")
        frame = self.eth_model.get_rx_frame(self.get_eth_id(qemu))
        self.set_mblk(qemu, m_blk, frame)
        return qemu.call("muxReceive", [self.p_dev, m_blk], self, "receive_done")

    # Method is broken, using multiple instance variable that are not defined
    # @bp_handler(["call_muxReceive"])
    # def call_mux_receive(self, qemu, bp_addr):  # pylint: disable=unused-argument
    #     """call_mux_receive"""
    #     eth_id = self.get_eth_id(qemu)
    #     frame = self.eth_model.get_rx_frame(eth_id)
    #     qemu.write_memory(self.p_buff, 1, frame, raw=True)
    #     # TODO set mBlk fields
    #     parsed_mblk = self.get_packetdata(qemu, self.p_m_blk)
    #     log.debug("MBLK: %s", parsed_mblk)
    #     self.eth_model.tx_frame(eth_id, parsed_mblk["mBlkHdr"]["PKT_DATA"])
    #     # input("Press Enter To Continue")
    #     return qemu.call("muxReceive", [self.p_dev, self.p_m_blk], self, "receive_done")

    @bp_handler(["receive_done"])
    def receive_done(self, qemu: HALQemuTarget, bp_addr: int) -> Tuple[bool, None]:  # pylint: disable=unused-argument
        """receive_done"""
        log.debug(
            "DONE With MuxReceive ...................................................."
        )
        eth_id = self.get_eth_id(qemu)
        self.eth_model.enable_rx_isr_bp(eth_id)
        return True, None
