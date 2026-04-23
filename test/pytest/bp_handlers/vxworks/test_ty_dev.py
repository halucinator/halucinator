"""Tests for halucinator.bp_handlers.vxworks.ty_dev"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.ty_dev import TYDev, TYIsrState
from halucinator.bp_handlers.vxworks.ios_dev import IosDev


class TestTYIsrState:
    def test_init(self):
        state = TYIsrState(tty_dev_struct=0x1000, dev_id="/tyCo/0", read_limit=10)
        assert state.tty_dev_struct == 0x1000
        assert state.dev_id == "/tyCo/0"
        assert state.read_limit == 10


class TestTYDev:
    @pytest.fixture(autouse=True)
    def reset_ios_drivers(self):
        original = IosDev.drivers.copy()
        yield
        IosDev.drivers = original

    def test_init_defaults(self):
        handler = TYDev()
        assert handler.tty_dev_offset == 0x10
        assert handler.sema_ptr_offset == 0x650
        assert handler.ird == "tyIRd"
        assert handler.use_rx_task is False
        assert handler.state_stack == []
        assert handler.done_stack == []
        assert handler.ioctl_options == 0

    def test_init_with_interfaces(self):
        model = mock.Mock()
        handler = TYDev(
            model=model,
            interfaces={"/tyCo/0": {"irq_num": 0x2, "enabled": True}},
        )
        model.add_interface.assert_called_once_with("/tyCo/0", irq_num=0x2, enabled=True)

    def test_init_custom_params(self):
        handler = TYDev(tty_dev_offset=0x20, sema_ptr_offset=0x700, ird="customIRd", use_rx_task=True)
        assert handler.tty_dev_offset == 0x20
        assert handler.sema_ptr_offset == 0x700
        assert handler.ird == "customIRd"
        assert handler.use_rx_task is True

    def test_get_utty_id_via_dev_hdr(self, qemu):
        handler = TYDev()
        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.read_memory = mock.Mock(return_value=b'\x00\x50\x00\x00')  # 0x5000 little-endian

        result = handler.get_utty_id(qemu, 0x1000)
        assert result == "/tyCo/0"

    def test_get_utty_id_via_p_ty_dev(self, qemu):
        handler = TYDev()
        IosDev.drivers[0x1000] = "/tyCo/1"

        # First read_memory returns a p_dev_hdr not in drivers
        qemu.read_memory = mock.Mock(return_value=b'\x00\x90\x00\x00')

        result = handler.get_utty_id(qemu, 0x1000)
        assert result == "/tyCo/1"

    def test_get_utty_id_not_found_raises(self, qemu):
        handler = TYDev()
        IosDev.drivers = {}

        qemu.read_memory = mock.Mock(return_value=b'\x00\x90\x00\x00')

        with pytest.raises(Exception, match="driver not found"):
            handler.get_utty_id(qemu, 0x1000)

    def test_receive_done(self, qemu):
        handler = TYDev()
        result = handler.receive_done(qemu, 0x1000)
        assert result == (True, None)

    def test_task_receive_done(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        iface = mock.Mock()
        iface.irq_num = 5
        handler.utty_model.interfaces = {"/tyCo/0": iface}
        handler.done_stack.append(TYIsrState(0x2000, "/tyCo/0", 0))

        result = handler.task_receive_done(qemu, 0x1000)

        assert result == (True, None)
        qemu.irq_enable_bp.assert_called_once_with(5)

    def test_ty_it_x(self, qemu):
        handler = TYDev()
        result = handler.ty_it_x(qemu, 0x1000)
        assert result == (False, None)

    def test_ty_ir_d(self, qemu):
        handler = TYDev()
        result = handler.ty_ir_d(qemu, 0x1000)
        assert result == (False, None)

    def test_ty_read(self, qemu):
        handler = TYDev()
        result = handler.ty_read(qemu, 0x1000)
        assert result == (False, None)

    def test_ty_write(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        IosDev.drivers[0x5000] = "/tyCo/0"

        def get_arg_side_effect(n):
            return [0x1000, 0x3000, 5][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # get_utty_id: p_dev_hdr
            b'hello',              # buf read
        ])

        result = handler.ty_write(qemu, 0x1000)

        assert result == (True, 5)
        handler.utty_model.tx_buf.assert_called_once()

    def test_fio_n_read(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 10
        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.read_memory = mock.Mock(return_value=b'\x00\x50\x00\x00')

        result = handler.fio_n_read(qemu, 0x1000, 0x6000)

        assert result == (True, 0)
        qemu.write_memory.assert_called_once_with(0x6000, 2, 10)

    def test_fio_rflush(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.read_memory = mock.Mock(return_value=b'\x00\x50\x00\x00')

        result = handler.fio_rflush(qemu, 0x1000, 0x6000)

        assert result == (True, 0)
        handler.utty_model.flush.assert_called_once()

    def test_fio_setoptions(self, qemu):
        handler = TYDev()
        result = handler.fio_setoptions(qemu, 0x1000, 0x42)
        assert result == (True, 0)
        assert handler.ioctl_options == 0x42

    def test_fio_getoptions(self, qemu):
        handler = TYDev()
        handler.ioctl_options = 0x42
        result = handler.fio_getoptions(qemu, 0x1000, 0x6000)
        assert result == (True, 0)
        qemu.write_memory.assert_called_once_with(0x6000, 4, 0x42)

    def test_ty_ioctl_with_function_handler(self, qemu):
        handler = TYDev()
        handler.ioctl_options = 0

        # func=3 is fio_setoptions
        def get_arg_side_effect(n):
            return [0x1000, 3, 0x42][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.ty_ioctl(qemu, 0x1000)
        assert result == (True, 0)
        assert handler.ioctl_options == 0x42

    def test_ty_ioctl_with_string_handler(self, qemu):
        handler = TYDev()

        # func=2 is "FIOFLUSH" (string, unimplemented)
        def get_arg_side_effect(n):
            return [0x1000, 2, 0x0][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.ty_ioctl(qemu, 0x1000)
        assert result == (False, None)

    def test_ty_ioctl_undefined(self, qemu):
        handler = TYDev()

        # func=999 is not in switcher
        def get_arg_side_effect(n):
            return [0x1000, 999, 0x0][n]
        qemu.get_arg = mock.Mock(side_effect=get_arg_side_effect)

        result = handler.ty_ioctl(qemu, 0x1000)
        assert result == (False, None)

    def test_ty_isr_with_data_no_rx_task(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 1
        handler.utty_model.get_rx_char.return_value = ord('A')
        handler.use_rx_task = False

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        # ty_isr reads: (1) get_utty_id reads p_dev_hdr at (p_ty_dev + 0xC) raw bytes
        #               (2) sema_val at (p_ty_dev + sema_ptr_offset)
        #               (3) tty_dev_struct at (p_ty_dev + tty_dev_offset)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id (raw bytes)
            0,                     # sema_val
            0x2000,                # tty_dev_struct
        ])

        result = handler.ty_isr(qemu, 0x1000)

        # Should call qemu.call with ird
        qemu.call.assert_called_once()

    def test_isr_execute_read_last_char(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_char.return_value = ord('B')

        state = TYIsrState(0x2000, "/tyCo/0", 1)
        handler.state_stack.append(state)

        result = handler.isr_execute_read(qemu, 0x1000)

        qemu.call.assert_called_once()
        # After consuming, read_limit should be 0
        assert state.read_limit == 0

    def test_isr_execute_read_more_chars(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_char.return_value = ord('C')

        state = TYIsrState(0x2000, "/tyCo/0", 3)
        handler.state_stack.append(state)

        result = handler.isr_execute_read(qemu, 0x1000)

        qemu.call.assert_called_once()
        # Should have pushed back a state with decremented limit
        assert len(handler.state_stack) == 1
        assert handler.state_stack[0].read_limit == 2

    def test_rx_task_no_data(self, qemu):
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 0

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id
            0x2000,                # tty_dev_struct
        ])

        result = handler.rx_task(qemu, 0x1000)
        assert result == (True, None)

    def test_ty_isr_multiple_chars(self, qemu):
        """Test ty_isr with num_chars_rx > 1 so it enters the isr_execute_read path."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 3
        handler.utty_model.get_rx_char.return_value = ord('A')
        handler.use_rx_task = False

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id
            0,                     # sema_val
            0x2000,                # tty_dev_struct
        ])

        result = handler.ty_isr(qemu, 0x1000)

        # With 3 chars, read_limit decremented to 2, should go to isr_execute_read
        qemu.call.assert_called_once()
        assert len(handler.state_stack) == 1

    def test_ty_isr_semaphore_path(self, qemu):
        """Test ty_isr when sema_val != 0 and use_rx_task is True."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 1
        handler.utty_model.get_rx_char.return_value = ord('A')
        handler.use_rx_task = True

        iface = mock.Mock()
        iface.irq_num = 5
        handler.utty_model.interfaces = {"/tyCo/0": iface}

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id
            0x9000,                # sema_val (nonzero)
            0x2000,                # tty_dev_struct
        ])

        result = handler.ty_isr(qemu, 0x1000)

        # Should call semGive
        qemu.call.assert_called_once()
        qemu.irq_disable_bp.assert_called_once_with(5)

    def test_rx_task_with_data(self, qemu):
        """Test rx_task when there is data to read."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 1
        handler.utty_model.get_rx_char.return_value = ord('X')

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id
            0x2000,                # tty_dev_struct
        ])

        result = handler.rx_task(qemu, 0x1000)

        # With exactly 1 char, read_limit becomes 0, should call task_receive_done
        qemu.call.assert_called_once()
        assert len(handler.done_stack) == 1

    def test_rx_task_multiple_chars(self, qemu):
        """Test rx_task with multiple chars to read."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_buff_size.return_value = 3
        handler.utty_model.get_rx_char.return_value = ord('Y')

        IosDev.drivers[0x5000] = "/tyCo/0"

        qemu.get_arg = mock.Mock(return_value=0x1000)
        qemu.read_memory = mock.Mock(side_effect=[
            b'\x00\x50\x00\x00',  # p_dev_hdr for get_utty_id
            0x2000,                # tty_dev_struct
        ])

        result = handler.rx_task(qemu, 0x1000)

        qemu.call.assert_called_once()
        assert len(handler.state_stack) == 1

    def test_execute_read_last_char(self, qemu):
        """Test task_execute_read with last character."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_char.return_value = ord('Z')

        state = TYIsrState(0x2000, "/tyCo/0", 1)
        handler.state_stack.append(state)

        result = handler.execute_read(qemu, 0x1000)

        qemu.call.assert_called_once()

    def test_execute_read_more_chars(self, qemu):
        """Test task_execute_read with more chars to read."""
        handler = TYDev()
        handler.utty_model = mock.Mock()
        handler.utty_model.get_rx_char.return_value = ord('W')

        state = TYIsrState(0x2000, "/tyCo/0", 3)
        handler.state_stack.append(state)

        result = handler.execute_read(qemu, 0x1000)

        qemu.call.assert_called_once()
        assert len(handler.state_stack) == 1
        assert handler.state_stack[0].read_limit == 2
