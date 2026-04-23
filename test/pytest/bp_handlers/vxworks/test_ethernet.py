"""Tests for halucinator.bp_handlers.vxworks.ethernet"""
import types
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.ethernet import Ethernet


class TestEthernet:
    def test_init_defaults(self):
        eth = Ethernet()
        assert eth.mac == b""
        assert eth.p_dev is None
        assert eth.p_net_pool is None
        assert eth.cl_pool_id is None
        assert eth.net_pool_offset == 0x2AC
        assert eth.cl_pool_id_offset == 0x33C
        assert eth.handle_end_int_rcv_addr is None

    def test_init_with_interfaces(self):
        model = mock.Mock()
        eth = Ethernet(
            model=model,
            interfaces={"eth0": {"irq_num": 0x19, "enabled": True}},
        )
        model.add_interface.assert_called_once_with("eth0", irq_num=0x19, enabled=True)

    def test_register_handler_sets_offsets(self, qemu):
        eth = Ethernet()
        eth.register_handler(qemu, 0x1000, "xSend",
                             net_pool_offset=0x300, cl_pool_id_offset=0x400)
        assert eth.net_pool_offset == 0x300
        assert eth.cl_pool_id_offset == 0x400

    def test_register_handler_default_offsets(self, qemu):
        eth = Ethernet()
        eth.register_handler(qemu, 0x1000, "xSend")
        assert eth.net_pool_offset == 0x2AC
        assert eth.cl_pool_id_offset == 0x33C

    def test_register_handler_end_handle_rcv(self, qemu):
        eth = Ethernet()
        qemu.avatar.config.get_addr_for_symbol.return_value = 0xBEEF0000

        eth.register_handler(qemu, 0x1000, "handleEndIntRcv",
                             end_handle_rcv="someSymbol")

        assert eth.handle_end_int_rcv_addr == 0xBEEF0000

    def test_register_handler_end_handle_rcv_not_found(self, qemu):
        eth = Ethernet()
        qemu.avatar.config.get_addr_for_symbol.return_value = None

        with pytest.raises(ValueError, match="Could not find address"):
            eth.register_handler(qemu, 0x1000, "handleEndIntRcv",
                                 end_handle_rcv="missingSymbol")

    def test_net_pool_init(self, qemu):
        eth = Ethernet()
        qemu.regs.r0 = 0x5000
        qemu.regs.lr = 0x8000

        with mock.patch("builtins.open", mock.mock_open()):
            result = eth.net_pool_init(qemu, 0x1000)

        assert result == (False, None)

    def test_get_eth_id(self, qemu):
        eth = Ethernet()
        assert eth.get_eth_id(qemu) == "eth0"

    def test_e_io_cs_addr(self, qemu):
        eth = Ethernet()
        qemu.read_memory = mock.Mock(return_value=b'\x00\x11\x22\x33\x44\x55\x00\x00\x00\x00')

        result = eth.e_io_cs_addr(qemu, 0x1000, 0x2000)

        assert result == (True, 0)
        assert eth.mac == b'\x00\x11\x22\x33\x44\x55\x00\x00\x00\x00'

    def test_e_io_cg_addr(self, qemu):
        eth = Ethernet()
        eth.mac = b'\x00\x11\x22\x33\x44\x55'

        result = eth.e_io_cg_addr(qemu, 0x1000, 0x2000)

        assert result == (True, 0)
        qemu.write_memory.assert_called_once_with(0x2000, 1, b'\x00\x11\x22\x33\x44\x55', 6, raw=True)

    def test_e_io_cs_flags(self, qemu):
        eth = Ethernet()
        qemu.read_memory = mock.Mock(return_value=b'\xFF' * 10)

        result = eth.e_io_cs_flags(qemu, 0x1000, 0x2000)

        assert result == (True, 0)
        assert eth.mac == b'\xFF' * 10

    def test_x_send(self, qemu):
        eth = Ethernet()
        eth.eth_model = mock.Mock()

        # Mock get_packetdata
        pkt_data = b'\x00\x11\x22\x33\x44\x55'
        with mock.patch.object(eth, 'get_packetdata', return_value={
            "mBlkHdr": {"PKT_DATA": pkt_data},
        }):
            result = eth.x_send(qemu, 0x1000)

        assert result == (False, 0)
        eth.eth_model.tx_frame.assert_called_once_with("eth0", pkt_data)

    def test_x_unload(self, qemu):
        eth = Ethernet()
        with mock.patch("builtins.input", return_value=""):
            result = eth.x_unload(qemu, 0x1000)
        assert result == (False, 0)

    def test_x_ioctl_function_handler(self, qemu):
        eth = Ethernet()
        qemu.regs.r0 = 0x1000
        qemu.regs.r1 = 0x40046912  # e_io_cs_addr
        qemu.regs.r2 = 0x2000
        qemu.read_memory = mock.Mock(return_value=b'\x00' * 10)

        result = eth.x_ioctl(qemu, 0x1000)

        assert result == (True, 0)

    def test_x_ioctl_string_handler(self, qemu):
        eth = Ethernet()
        qemu.regs.r0 = 0x1000
        qemu.regs.r1 = 0x40046910  # "EIOCGFLAGS" (string)
        qemu.regs.r2 = 0x2000

        result = eth.x_ioctl(qemu, 0x1000)

        assert result == (False, 0)

    def test_x_ioctl_undefined(self, qemu):
        eth = Ethernet()
        qemu.regs.r0 = 0x1000
        qemu.regs.r1 = 0xDEADBEEF  # Not in switcher
        qemu.regs.r2 = 0x2000
        qemu.regs.lr = 0x8000

        result = eth.x_ioctl(qemu, 0x1000)

        assert result == (False, 0)

    def test_x_poll_send(self, qemu):
        eth = Ethernet()
        with mock.patch("builtins.input", return_value=""):
            result = eth.x_poll_send(qemu, 0x1000)
        assert result == (False, 0)

    def test_x_start(self, qemu):
        eth = Ethernet()
        eth.eth_model = mock.Mock()

        result = eth.x_start(qemu, 0x1000)

        assert result == (False, 0)
        eth.eth_model.flush.assert_called_once_with("eth0")
        eth.eth_model.enable.assert_called_once_with("eth0")

    def test_x_load(self, qemu):
        eth = Ethernet()
        result = eth.x_load(qemu, 0x1000)
        assert result == (False, 0)

    def test_x_m_cast_addr_del(self, qemu):
        eth = Ethernet()
        result = eth.x_m_cast_addr_del(qemu, 0x1000)
        assert result == (False, 0)

    def test_x_m_cast_addr_get(self, qemu):
        eth = Ethernet()
        result = eth.x_m_cast_addr_get(qemu, 0x1000)
        assert result == (False, 0)

    def test_x_m_cast_addr_add(self, qemu):
        eth = Ethernet()
        result = eth.x_m_cast_addr_add(qemu, 0x1000)
        assert result == (False, 0)

    def test_ethernet_isr(self, qemu):
        eth = Ethernet()
        eth.handle_end_int_rcv_addr = 0xBEEF0000
        qemu.get_arg = mock.Mock(return_value=0x1000)

        result = eth.ethernet_isr(qemu, 0x1000)

        qemu.call.assert_called_once_with(
            "netJobAdd", [0xBEEF0000, 0x1000, 0, 0, 0, 0]
        )

    def test_handle_end_int_rcv(self, qemu):
        eth = Ethernet()
        eth.net_pool_offset = 0x2AC
        eth.cl_pool_id_offset = 0x33C

        qemu.get_arg = mock.Mock(return_value=0x4000)
        qemu.read_memory = mock.Mock(side_effect=[0x5000, 0x6000])

        result = eth.handle_end_int_rcv(qemu, 0x1000)

        assert eth.p_dev == 0x4000
        assert eth.p_net_pool == 0x5000
        assert eth.cl_pool_id == 0x6000
        qemu.call.assert_called_once()

    def test_set_mblk(self, qemu):
        eth = Ethernet()
        data = b'\xDE\xAD\xBE\xEF'

        # set_mblk reads: m_data, flags; also calls get_packetdata which we mock
        qemu.read_memory = mock.Mock(side_effect=[
            0x9000,  # m_data ptr
            0x01,    # flags at mblk + 0x12
        ])

        with mock.patch.object(eth, 'get_packetdata', return_value={}):
            eth.set_mblk(qemu, 0x8000, data)

        # Should write data, m_len, pktHdr, and flags
        assert qemu.write_memory.call_count >= 4

    def test_get_mblk(self, qemu):
        eth = Ethernet()
        eth.eth_model = mock.Mock()
        eth.p_dev = 0x4000
        eth.eth_model.get_rx_frame.return_value = b'\x01\x02\x03\x04'

        qemu.regs.r0 = 0x8000

        with mock.patch.object(eth, 'set_mblk'):
            result = eth.get_mblk(qemu, 0x1000)

        eth.eth_model.get_rx_frame.assert_called_once_with("eth0")
        qemu.call.assert_called_once()

    def test_get_mblk_null_raises(self, qemu):
        eth = Ethernet()
        qemu.regs.r0 = 0  # null mblk

        with pytest.raises(TypeError, match="Failed to get netTuple"):
            eth.get_mblk(qemu, 0x1000)

    def test_receive_done(self, qemu):
        eth = Ethernet()
        eth.eth_model = mock.Mock()

        result = eth.receive_done(qemu, 0x1000)

        assert result == (True, None)
        eth.eth_model.enable_rx_isr_bp.assert_called_once_with("eth0")

    def test_get_packetdata(self, qemu):
        """get_packetdata has a bug at line 150 where m_data is accessed before
        being set. We test it raises KeyError to document this."""
        eth = Ethernet()
        qemu.read_memory = mock.Mock(return_value=0x100)

        with pytest.raises(KeyError):
            eth.get_packetdata(qemu, 0x8000)
