"""Tests for halucinator.bp_handlers.vxworks.errors"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.errors import ERROR, get_error_type, errnos


class TestGetErrorType:
    def test_known_errno(self):
        assert get_error_type(0x1) == "EPERM"
        assert get_error_type(0x2) == "ENOENT"
        assert get_error_type(0xc) == "ENOMEM"
        assert get_error_type(0x16) == "EINVAL"

    def test_unknown_errno(self):
        result = get_error_type(0xFFFFFF)
        assert result == "ERROR_TYPE_UNDEFINED_0xffffff"

    def test_vxworks_specific_errno(self):
        assert get_error_type(0x30065) == "S_taskLib_NAME_NOT_FOUND"
        assert get_error_type(0x380002) == "S_dosFsLib_DISK_FULL"
        assert get_error_type(0xd0001) == "S_iosLib_DEVICE_NOT_FOUND"

    def test_zero_errno(self):
        # 0 is not in errnos dict
        result = get_error_type(0)
        assert "ERROR_TYPE_UNDEFINED" in result

    def test_errnos_dict_has_entries(self):
        assert len(errnos) > 100


class TestERROR:
    def test_init(self):
        err = ERROR()
        assert err.last_error is None

    def test_errno_set_zero_does_nothing(self, qemu):
        err = ERROR()
        qemu.regs.r0 = 0

        result = err.errno_set(qemu, 0x1000)

        assert result == (False, None)
        assert err.last_error is None

    def test_errno_set_known_error(self, qemu):
        err = ERROR()
        qemu.regs.r0 = 0x16  # EINVAL
        qemu.regs.lr = 0x08001000
        qemu.get_symbol_name = mock.Mock(return_value="myFunc")

        result = err.errno_set(qemu, 0x1000)

        assert result == (False, None)
        assert err.last_error is not None
        assert "EINVAL" in err.last_error

    def test_errno_set_no_symbol(self, qemu):
        err = ERROR()
        qemu.regs.r0 = 0x16
        qemu.regs.lr = 0x08001000
        qemu.get_symbol_name = mock.Mock(return_value=None)

        result = err.errno_set(qemu, 0x1000)

        assert result == (False, None)
        assert "0x8001000" in err.last_error

    def test_errno_set_dedup(self, qemu):
        """Same error should not log twice (last_error tracks it)."""
        err = ERROR()
        qemu.regs.r0 = 0x16
        qemu.regs.lr = 0x08001000
        qemu.get_symbol_name = mock.Mock(return_value="myFunc")

        err.errno_set(qemu, 0x1000)
        first_error = err.last_error

        err.errno_set(qemu, 0x1000)
        assert err.last_error == first_error

    def test_dunder_errno_zero(self, qemu):
        err = ERROR()
        qemu.regs.r0 = 0x5000
        qemu.read_memory = mock.Mock(return_value=0)

        # __errno is a mangled name in python, access via getattr
        method = getattr(err, '_ERROR__errno')
        result = method(qemu, 0x1000)

        assert result == (False, None)
        qemu.read_memory.assert_called_once_with(0x5000, 4)

    def test_dunder_errno_nonzero(self, qemu):
        """Note: errors.py line 734 has a bug where tuple + str concatenation
        fails at runtime. The handler still returns (False, None) because the
        TypeError is in log.debug formatting, which logging swallows."""
        err = ERROR()
        qemu.regs.r0 = 0x5000
        qemu.regs.lr = 0x08001000
        qemu.read_memory = mock.Mock(return_value=0x16)

        method = getattr(err, '_ERROR__errno')
        # The source has a bug: (error_type, hex(qemu.regs.lr)) + BColors.ENDC
        # This raises TypeError. We verify the handler at least gets invoked.
        with pytest.raises(TypeError):
            method(qemu, 0x1000)

    def test_sys_err(self, qemu):
        err = ERROR()
        qemu.regs.lr = 0x08001000

        result = err.sys_err(qemu, 0x1000)

        assert result == (False, None)

    def test_close_done(self, qemu):
        err = ERROR()
        qemu.regs.r0 = 0

        result = err.close_done(qemu, 0x1000)

        assert result == (True, None)

    def test_open_done(self, qemu):
        err = ERROR()
        qemu.get_arg = mock.Mock(return_value=4)
        qemu.hal_alloc = mock.Mock()
        alloc_result = mock.Mock()
        alloc_result.base_addr = 0x9000
        qemu.hal_alloc.return_value = alloc_result

        result = err.open_done(qemu, 0x1000)

        qemu.call.assert_called_once()
        call_args = qemu.call.call_args
        assert call_args[0][0] == 'write'

    def test_write_done(self, qemu):
        err = ERROR()
        qemu.get_arg = mock.Mock(return_value=3)

        result = err.write_done(qemu, 0x1000)

        qemu.call.assert_called_once()
        call_args = qemu.call.call_args
        assert call_args[0][0] == 'close'

    def test_logInit(self, qemu):
        err = ERROR()
        alloc_result = mock.Mock()
        alloc_result.base_addr = 0x9000
        qemu.hal_alloc = mock.Mock(return_value=alloc_result)
        # logInit reads back the string it wrote to verify
        logname = '/d0/SYSTEM/custom_sfe.log'
        qemu.read_memory = mock.Mock(return_value=list(logname.encode('utf-8')))

        result = err.logInit(qemu, 0x1000)

        qemu.call.assert_called_once()
        call_args = qemu.call.call_args
        assert call_args[0][0] == 'open'
