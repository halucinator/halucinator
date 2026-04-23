"""Tests for halucinator.bp_handlers.vxworks.boot"""
from unittest import mock

import pytest

from halucinator.bp_handlers.vxworks.boot import Boot


class TestBoot:
    def test_init(self):
        boot = Boot()
        assert boot.bootline is None

    def test_register_handler_sets_bootline(self, qemu):
        boot = Boot()
        boot.register_handler(qemu, 0x1000, 'bootStringToStruct', bootline='test_boot')
        assert boot.bootline == 'test_boot\x00'

    def test_register_handler_already_null_terminated(self, qemu):
        boot = Boot()
        boot.register_handler(qemu, 0x1000, 'bootStringToStruct', bootline='test_boot\x00')
        assert boot.bootline == 'test_boot\x00'

    def test_register_handler_no_bootline_raises(self, qemu):
        boot = Boot()
        with pytest.raises(ValueError, match="bootline required"):
            boot.register_handler(qemu, 0x1000, 'bootStringToStruct')

    def test_register_handler_other_func_name(self, qemu):
        boot = Boot()
        # Only bootStringToStruct is a registered bp_handler;
        # other func names will raise ValueError from BPHandler base
        with pytest.raises(ValueError):
            boot.register_handler(qemu, 0x1000, 'otherFunc')
        assert boot.bootline is None

    def test_usr_boot_string_to_struct(self, qemu):
        boot = Boot()
        boot.bootline = 'myboot\x00'
        qemu.get_arg = mock.Mock(return_value=0x2000)

        result = boot.usr_boot_string_to_struct(qemu, 0x1000)

        assert result == (False, None)
        qemu.get_arg.assert_called_with(0)
        qemu.write_memory.assert_called_once_with(
            0x2000, 1, b'myboot\x00', 7, raw=True
        )

    def test_usr_boot_string_to_struct_long_bootline(self, qemu):
        boot = Boot()
        bootline = 'e(0,0)host:vxWorks h=192.168.1.1 e=192.168.1.2 u=user pw=pass\x00'
        boot.bootline = bootline
        qemu.get_arg = mock.Mock(return_value=0x3000)

        result = boot.usr_boot_string_to_struct(qemu, 0x1000)

        assert result == (False, None)
        qemu.write_memory.assert_called_once_with(
            0x3000, 1, bootline.encode(), len(bootline), raw=True
        )
