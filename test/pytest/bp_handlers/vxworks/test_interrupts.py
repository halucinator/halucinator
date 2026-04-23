"""Tests for halucinator.bp_handlers.vxworks.interrupts"""
import os
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.interrupts import Interrupts


class TestInterrupts:
    @pytest.fixture(autouse=True)
    def cleanup_log(self, tmp_path):
        """Use a temporary path for the int_connect log."""
        self.log_path = str(tmp_path / "intConnect.log")
        yield

    def _make_handler(self):
        model = mock.Mock()
        handler = Interrupts(model=model, int_connect_log=self.log_path)
        handler.model = model
        return handler

    def test_init_defaults(self):
        with mock.patch("builtins.open", mock.mock_open()):
            handler = Interrupts()
        assert handler.level == 2

    def test_init_custom(self):
        model = mock.Mock()
        handler = Interrupts(model=model, int_connect_log=self.log_path, level=3)
        assert handler.level == 3
        assert handler.model is model

    def test_int_lvl_vec_chk_with_irq(self, qemu):
        handler = self._make_handler()
        handler.model.get_first_irq.return_value = 42

        result = handler.int_lvl_vec_chk(qemu, 0x1000)

        assert result == (True, 0)
        # Writes level to arg0 and irq_num to arg1
        calls = qemu.write_memory.call_args_list
        assert len(calls) == 2
        # write level to get_arg(0)
        qemu.write_memory.assert_any_call(qemu.get_arg(0), 4, handler.level)
        # write irq to get_arg(1)
        qemu.write_memory.assert_any_call(qemu.get_arg(1), 4, 42)
        handler.model.clear_active_bp.assert_called_once_with(42)

    def test_int_lvl_vec_chk_no_irq(self, qemu):
        handler = self._make_handler()
        handler.model.get_first_irq.return_value = None

        result = handler.int_lvl_vec_chk(qemu, 0x1000)

        assert result == (True, 0)
        qemu.write_memory.assert_not_called()
        handler.model.clear_active_bp.assert_not_called()

    def test_int_connect(self, qemu):
        handler = self._make_handler()
        qemu.regs.r0 = 0x20
        qemu.regs.lr = 0x08001000

        result = handler.int_connect(qemu, 0x1000)

        assert result == (False, None)
        # Should write to log file
        with open(self.log_path, 'r') as f:
            content = f.read()
        assert "caller:" in content
        assert "vector:" in content

    def test_int_connect_no_symbol(self, qemu):
        handler = self._make_handler()
        qemu.regs.r0 = 0x20
        qemu.regs.lr = 0x08001000
        qemu.avatar.config.get_symbol_name = mock.Mock(return_value=None)

        result = handler.int_connect(qemu, 0x1000)

        assert result == (False, None)

    def test_int_exit(self, qemu):
        handler = self._make_handler()
        result = handler.int_exit(qemu, 0x1000)
        assert result == (False, None)

    def test_int_enable(self, qemu):
        handler = self._make_handler()
        qemu.get_arg = mock.Mock(return_value=5)

        result = handler.int_enable(qemu, 0x1000)

        assert result == (False, None)
        handler.model.enable_bp.assert_called_once_with(5)

    def test_int_disable(self, qemu):
        handler = self._make_handler()
        qemu.get_arg = mock.Mock(return_value=5)

        result = handler.int_disable(qemu, 0x1000)

        assert result == (False, None)
        handler.model.disable_bp.assert_called_once_with(5)

    def test_int_lock_level_set(self, qemu):
        handler = self._make_handler()
        result = handler.int_lock_level_set(qemu, 0x1000)
        assert result == (False, None)

    def test_int_lock_level_get(self, qemu):
        handler = self._make_handler()
        result = handler.int_lock_level_get(qemu, 0x1000)
        assert result == (False, None)

    def test_int_lock(self, qemu):
        handler = self._make_handler()
        result = handler.int_lock(qemu, 0x1000)
        assert result == (False, None)

    def test_int_unlock(self, qemu):
        handler = self._make_handler()
        result = handler.int_unlock(qemu, 0x1000)
        assert result == (False, None)
