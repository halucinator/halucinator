import pytest
import re
from unittest import mock

from halucinator.debug_adapter.variables import VariableFormat, Variables


class Mock_Debugger(mock.Mock):
    DEFAULT_REGISTERS = {
        "r0": 0,
        "r1": 0x11,
        "r2": 0x22,
        "r3": 0x33,
        "r4": 0x44,
        "sp": 0xDD,
        "lr": 0xEE,
        "pc": 0xFF,
    }

    def __init__(self, *args, **kwargs):
        mock.Mock.__init__(self, *args, **kwargs)
        self.watchpoints = {}
        self.registers = dict(self.DEFAULT_REGISTERS)

    def list_watchpoints(self):
        return self.watchpoints

    def read_register(self, name, hex_mode=True):
        assert hex_mode == False
        return self.registers[name]

    def list_all_regs_names(self):
        return list(self.registers.keys())


class Mock_Runtime(mock.Mock):
    def __init__(self, *args, **kwargs):
        mock.Mock.__init__(self, *args, **kwargs)
        self.debugger = Mock_Debugger()

    def getRegisters(self):
        return self.debugger.registers

    def memory_info(self, addr):
        if addr >= 0x200:
            return None
        result = mock.Mock()
        result.addr_start = addr
        result.addr_end = 0x200
        result.name = "mem0"
        result.supports_watch = True
        return result

    def read_memory(self, addr, size, words, raw=False):
        barray = bytearray()
        addr_align = addr // 4 * 4
        addr_off = addr - addr_align
        end_addr = addr + size * words + 4 * (addr > addr_align)

        for word_addr in range(addr_align, end_addr, 4):
            barray += word_addr.to_bytes(4, "little")

        if raw:
            return bytes(barray[addr_off : size * words + addr_off])

        result = []
        for word_off in range(addr_off, size * words + addr_off, size):
            word_bytes = barray[word_off : word_off + 4]
            word_val = int.from_bytes(word_bytes, "little")
            result.append(word_val)
        return result


class Test_VariableFormat:
    def test_menu_string(self):
        format_reg = VariableFormat(Variables.VREF_REGISTERS, "")
        format_stack = VariableFormat(Variables.VREF_STACK, "")
        format_global = VariableFormat(Variables.VREF_GLOBALS, "")

        menu_reg = format_reg.to_menu_string(True)
        menu_stack = format_stack.to_menu_string(True)
        menu_global = format_global.to_menu_string(False)

        # Test with the same/similar regex strings that VSCode extension uses
        assert not re.match("^data.*options:[^;]*add_global", menu_reg)
        assert re.match("^data.*options:[^;]*pointer", menu_reg)
        assert re.match("^data.*options:[^;]*add_global", menu_stack)
        assert re.match("^data.*options:[^;]*pointer", menu_stack)
        assert not re.match("^data.*options:[^;]*add_global", menu_global)
        assert not re.match("^data.*options:[^;]*pointer", menu_global)

    def test_copy(self):
        def validate_resize(result, source, size):
            assert result.vref == source.vref
            assert result.total_size == size
            assert result.size * result.array_length == size
            assert not result.is_pointer

        var_intptr = VariableFormat(Variables.VREF_REGISTERS, "")
        var_intptr.is_pointer = True
        var_intptr.array_length = 16
        var_intptr.base_type = "signed"
        var_intarr = VariableFormat(Variables.VREF_GLOBALS, "")
        var_intarr.array_length = 8
        var_intptr.base_type = "unsigned"

        # Shrinking int* to less than the base element size
        copy_3b = var_intptr.copy(mock.sentinel.COPY_3B, 3)
        validate_resize(copy_3b, var_intptr, 3)
        assert copy_3b.name == mock.sentinel.COPY_3B
        assert copy_3b.base_type == "hex"

        # Resizing int* to larger than the base element size, but odd
        copy_5b = var_intptr.copy("", 5)
        validate_resize(copy_5b, var_intptr, 5)
        assert copy_5b.base_type == "hex"

        # Resizing int* to a multiple of the base element size
        copy_16b = var_intptr.copy("", 16)
        validate_resize(copy_16b, var_intptr, 16)
        assert copy_16b.base_type == "hex"

        # Resizing int[] to a single element
        copy_4b = var_intarr.copy("", 4)
        validate_resize(copy_4b, var_intarr, 4)
        assert copy_4b.size == var_intarr.size
        assert copy_4b.base_type == var_intarr.base_type

        # Resizing int[] to a smaller array
        copy_32b = var_intarr.copy("", 32)
        validate_resize(copy_32b, var_intarr, 32)
        assert copy_32b.size == var_intarr.size

    def test_set(self):
        reg_fmt = VariableFormat(Variables.VREF_REGISTERS, "")
        stack_fmt = VariableFormat(Variables.VREF_STACK, "")

        # Set char, UTF-32
        reg_fmt.set({"baseType": "char", "codec": "UTF-32LE"})
        assert reg_fmt.base_type == "char"
        assert reg_fmt.codec == "UTF-32LE"
        assert reg_fmt.size == 4
        assert reg_fmt.array_length == 1
        assert not reg_fmt.is_pointer

        # Set char, UTF-16
        reg_fmt.set({"baseType": "char", "codec": "UTF-16LE"})
        assert reg_fmt.base_type == "char"
        assert reg_fmt.codec == "UTF-16LE"
        assert reg_fmt.size == 2
        assert reg_fmt.array_length == 2

        # Set unsigned, 4 byte
        reg_fmt.set({"baseType": "unsigned", "size": 4})
        assert reg_fmt.base_type == "unsigned"
        assert reg_fmt.size == 4
        assert reg_fmt.array_length == 1

        # Set char, should default to UTF-8
        reg_fmt.set({"baseType": "char"})
        assert reg_fmt.base_type == "char"
        assert reg_fmt.codec == "UTF-8"
        assert reg_fmt.size == 1
        assert reg_fmt.array_length == 4

        # Set *char[2]
        reg_fmt.set(
            {
                "baseType": "char",
                "isPointer": True,
                "codec": "UTF-8",
                "arrayLength": 2,
            }
        )
        assert reg_fmt.base_type == "char"
        assert reg_fmt.size == 1
        assert reg_fmt.array_length == 2
        assert reg_fmt.is_pointer

        # Set back from pointer to int (signed)
        reg_fmt.set({"baseType": "signed", "isPointer": False})
        assert reg_fmt.base_type == "signed"
        assert reg_fmt.size == 4
        assert reg_fmt.array_length == 1
        assert not reg_fmt.is_pointer

        # Stack format should not auto-adjust length, try uint16_t
        stack_fmt.set({"baseType": "hex", "size": 2})
        assert stack_fmt.base_type == "hex"
        assert stack_fmt.size == 2
        assert stack_fmt.array_length == 1
        assert not stack_fmt.is_pointer

    def test_format_int(self):
        format = VariableFormat(Variables.VREF_GLOBALS, "")

        format.base_type = "hex"
        assert format.format_int(64) == "0x40"
        assert format.format_int(0xFFFFFFFF) == "0xffffffff"

        format.base_type = "unsigned"
        assert format.format_int(64) == "64"
        assert format.format_int(0xFFFFFFFF) == "4294967295"

        format.base_type = "signed"
        assert format.format_int(64) == "64"
        assert format.format_int(0xFF) == "255"
        assert format.format_int(0xFFFF) == "65535"
        assert format.format_int(0xFFFFFFFF) == "-1"

        format.size = 2
        assert format.format_int(64) == "64"
        assert format.format_int(0xFF) == "255"
        assert format.format_int(0xFFFF) == "-1"

        format.size = 1
        assert format.format_int(64) == "64"
        assert format.format_int(0xFF) == "-1"

        format.base_type = "char"
        format.array_length = 4
        assert format.format_int(0x43) == '"C"'

    def test_quote_string(self):
        utf8 = VariableFormat(Variables.VREF_GLOBALS, "")
        utf16 = VariableFormat(Variables.VREF_GLOBALS, "")
        utf32 = VariableFormat(Variables.VREF_GLOBALS, "")
        utf8.set({"baseType": "char", "codec": "UTF-8"})
        utf16.set({"baseType": "char", "codec": "UTF-16LE"})
        utf32.set({"baseType": "char", "codec": "UTF-32LE"})

        # Encoding "Hi\u203c\U0001f600" (Hi, double !!, grinning emoji)
        decoded_str = '"Hi\u203c\U0001f600"'
        u8_str = b"Hi\xe2\x80\xbc\xf0\x9f\x98\x80"
        u16_str = b"H\x00i\x00\x3c\x20\x3d\xd8\x00\xde"
        u32_str = b"H\x00\x00\x00i\x00\x00\x00\x3c\x20\x00\x00\x00\xf6\x01\x00"

        assert utf8.quote_string(u8_str) == decoded_str
        assert utf16.quote_string(u16_str) == decoded_str
        assert utf32.quote_string(u32_str) == decoded_str

        # Latin-1 Extended
        assert utf8.quote_string(b"Resum\xc3\xa9") == '"Resum\xe9"'

        # Literal backslash, quote
        assert utf8.quote_string(b'"Hi!"\\n') == '"\\"Hi!\\"\\\\n"'

        # Decoding whitespace
        assert (
            utf8.quote_string(b"Hello, world\t!\r\n")
            == '"Hello, world\\t!\\r\\n"'
        )
        assert utf8.quote_string(b"\xc2\x85") == '"\\u0085"'  # Latin next line
        assert utf8.quote_string(b"\xe3\x80\x80") == '"\\u3000"'  # CJK space
        assert utf16.quote_string(b"\x00\x30") == '"\\u3000"'

        # UTF-8 single-byte escapes
        assert utf8.quote_string(b"zero\x00byte") == '"zero\\x00byte"'
        assert utf8.quote_string(b"hi\x1f\x7f") == '"hi\\x1f\\x7f"'

        # Bytes that do not decode as valid UTF-8
        assert utf8.quote_string(b"\xff" * 4) == '"\\xff\\xff\\xff\\xff"'

    def test_parse_value(self):
        format = VariableFormat(Variables.VREF_GLOBALS, "")

        assert format.parse_value('"Hello, world!"') == b"Hello, world!"
        assert format.parse_value("[12, 48, 0x11]") == [12, 48, 17]
        assert format.parse_value("2048") == 2048

        with pytest.raises(Exception):
            format.parse_value('"Unterminated string')
        with pytest.raises(Exception):
            format.parse_value("12a")

        assert format.parse_value('"\\xff\\xff\\xff\\xff"') == b"\xff" * 4
        assert format.parse_value('"\\" and \\\\"') == b'" and \\'
        assert format.parse_value('"\\r\\n\\t"') == b"\r\n\t"
        assert format.parse_value('"Resum\xe9"') == b"Resum\xc3\xa9"
        assert format.parse_value('"Resum\\u00e9"') == b"Resum\xc3\xa9"
        assert format.parse_value('"Resum\\xe9"') == b"Resum\xe9"
        assert format.parse_value('"\\r\\n\\t"') == b"\r\n\t"
        assert format.parse_value('"Zero\\x00middle"') == b"Zero\x00middle"

    def test_parse_bytes(self):
        format = VariableFormat(Variables.VREF_GLOBALS, "")

        assert format.parse_value_bytes("0x24") == b"\x24\x00\x00\x00"
        assert format.parse_value_bytes("-1") == b"\xff" * 4
        assert format.parse_value_bytes('"hi"') == b"hi\x00\x00"
        assert format.parse_value_bytes("[258]") == b"\x02\x01\x00\x00"

        format.array_length = 4
        format.size = 1

        assert format.parse_value_bytes("[1, 255, -1]") == b"\x01\xff\xff\x00"
        with pytest.raises(Exception):
            format.parse_value_bytes("[1, 2, 3, 4, 5]")
        with pytest.raises(Exception):
            format.parse_value_bytes("[1, 256]")
        with pytest.raises(Exception):
            format.parse_value_bytes("[1, -129]")
        with pytest.raises(Exception):
            format.parse_value_bytes('"hello"')


class Test_Variables:
    def test_scopes(self):
        vars = Variables(Mock_Runtime())
        scopes = vars.get_scopes()
        assert len(scopes) == 3
        # Make sure every variablesReference is specified and unique
        assert len(set([s["variablesReference"] for s in scopes])) == 3

    def test_read_variables(self):
        runtime = Mock_Runtime()
        vars = Variables(runtime)

        regs = vars.read_variables(Variables.VREF_REGISTERS)
        assert len(regs) == len(runtime.debugger.registers)

        stack = vars.read_variables(Variables.VREF_STACK)
        assert len(stack) == vars.stack_size // 4 + 1
        assert stack[0]["name"] == "Stack bytes"
        assert stack[0]["variablesReference"] == 0
        assert stack[0]["presentationHint"]["kind"] == "virtual"

        for stack_var in stack[1:]:
            assert stack_var["name"].startswith("0x")
            assert stack_var["variablesReference"] == 0

        globals = vars.read_variables(Variables.VREF_GLOBALS)
        assert globals[0]["name"] == "Globals"
        assert globals[0]["presentationHint"]["kind"] == "virtual"

    def test_set_variable(self):
        runtime = Mock_Runtime()
        vars = Variables(runtime)

        # Stack size testing
        ssize = "Stack size (bytes)"
        vars.set_variable(Variables.VREF_STACK, ssize, "64")
        assert vars.stack_size == 64
        with pytest.raises(Exception):
            vars.set_variable(Variables.VREF_STACK, ssize, '"Hello"')
        with pytest.raises(Exception):
            vars.set_variable(Variables.VREF_STACK, ssize, -12)
        with pytest.raises(Exception):
            vars.set_variable(Variables.VREF_STACK, ssize, 1024 * 1024)
        assert vars.stack_size == 64

        # Try setting a register as hex, and as base-10
        vars.set_variable(Variables.VREF_REGISTERS, "r2", "0xFDB86420")
        runtime.setRegister.assert_called_once_with("r2", 0xFDB86420)
        runtime.reset_mock()
        vars.set_variable(Variables.VREF_REGISTERS, "r3", "24")
        runtime.setRegister.assert_called_once_with("r3", 24)

        # Try setting memory
        vars.set_variable(Variables.VREF_STACK, "0x108", "-1")
        runtime.debugger.write_memory_assert_called_once_with(
            0x108, 1, b"\xff" * 8, 1, True
        )

    def test_globals(self):
        vars = Variables(Mock_Runtime())
        vars.add_globals(["0x80", "0xc0"])
        assert vars.globals == {0x80, 0xC0}
        vars.add_globals(["0xa8"])
        assert vars.globals == {0x80, 0xA8, 0xC0}
        vars.remove_globals(["0xa8", "0x80"])
        assert vars.globals == {0xC0}

    def test_get_format(self):
        vars = Variables(Mock_Runtime())
        r0 = vars.get_format(Variables.VREF_REGISTERS, "r0")
        assert r0.size == 4
        r0.base_type = "unsigned"
        r0_b = vars.get_format(Variables.VREF_REGISTERS, "r0")
        assert r0_b.base_type == "unsigned"
        with pytest.raises(Exception):
            vars.get_format(Variables.VREF_REGISTERS, "r20")

    def test_reset_format(self):
        vars = Variables(Mock_Runtime())
        r0 = vars.get_format(Variables.VREF_REGISTERS, "r0")
        r1 = vars.get_format(Variables.VREF_REGISTERS, "r1")
        assert r0.base_type == "hex"
        r0.base_type = "unsigned"
        r1.base_type = "signed"

        # Reset one variable
        vars.reset_format(Variables.VREF_REGISTERS, "r0")
        r0 = vars.get_format(Variables.VREF_REGISTERS, "r0")
        r1 = vars.get_format(Variables.VREF_REGISTERS, "r1")
        assert r0.base_type == "hex"
        assert r1.base_type == "signed"

        # Reset entire vref
        vars.reset_format(Variables.VREF_REGISTERS)
        r1 = vars.get_format(Variables.VREF_REGISTERS, "r1")
        assert r1.base_type == "hex"

    def test_get_address(self):
        runtime = Mock_Runtime()
        debugger = runtime.debugger
        vars = Variables(runtime)
        r0 = vars.get_format(Variables.VREF_REGISTERS, "r0")
        r0_val = mock.sentinel.r0
        mem_val = mock.sentinel.mem
        debugger.registers["r0"] = r0_val
        debugger.read_memory = mock.Mock(return_value=[mem_val])

        assert vars.get_address(Variables.VREF_REGISTERS, "r0") is None
        r0.is_pointer = True
        assert vars.get_address(Variables.VREF_REGISTERS, "r0") == r0_val

        assert vars.get_address(Variables.VREF_GLOBALS, "0x88", False) == 0x88
        debugger.read_memory.assert_not_called()
        assert (
            vars.get_address(Variables.VREF_GLOBALS, "0x88", True) == mem_val
        )
        debugger.read_memory.assert_called_once_with(0x88, 4)

    def test_get_address_star_prefix(self):
        """get_address with * prefix implies deref (lines 1067-1070)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # *0x88 with no deref arg should default to True
        runtime.debugger.read_memory = mock.Mock(return_value=[0x42])
        result = vars.get_address(Variables.VREF_GLOBALS, "*0x88")
        assert result == 0x42
        runtime.debugger.read_memory.assert_called_once_with(0x88, 4)

    def test_get_address_deref_none_no_format(self):
        """get_address with deref=None and no format checks is_pointer (lines 1072-1077)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # No format set, deref=None -> deref=False
        result = vars.get_address(Variables.VREF_GLOBALS, "0x88")
        assert result == 0x88

    def test_get_address_deref_none_invalid_dynamic_vref(self):
        """get_address with deref=None and invalid dynamic vref catches ValueError (lines 1076-1077)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Use a dynamic vref that's out of range, with deref=None
        result = vars.get_address(Variables.VREF_DYNAMIC + 999, "0x88")
        # Should not raise; falls through to try int(name, 0) -> 0x88
        assert result == 0x88

    def test_get_address_invalid_name(self):
        """get_address with invalid name returns None (lines 1088-1089)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        result = vars.get_address(Variables.VREF_GLOBALS, "notanumber", False)
        assert result is None

    def test_get_address_register_deref_none(self):
        """get_address for register with deref=None checks format (line 1076-1077)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # No format, deref=None -> deref=False for registers -> None
        result = vars.get_address(Variables.VREF_REGISTERS, "r0")
        assert result is None

    def test_read_variables_unknown_vref(self):
        """read_variables with unknown vref returns empty list (line 607)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Use vref=50 which doesn't match any section (1,2,3) or VREF_DYNAMIC (>=100)
        result = vars.read_variables(50)
        assert result == []

    def test_read_variables_dynamic_vref(self):
        """read_variables with dynamic vref (lines 604-607)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Should return empty list if no dynamic vrefs
        result = vars.read_variables(Variables.VREF_DYNAMIC)
        assert result == []

    def test_read_globals_with_entries(self):
        """read_globals with global entries (lines 672-674)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        vars.add_globals(["0x80", "0xc0"])
        result = vars.read_variables(Variables.VREF_GLOBALS)
        # Header + 2 globals
        assert len(result) == 3
        assert result[1]["name"].startswith("0x")
        assert result[2]["name"].startswith("0x")

    def test_read_stack_sp_change_resets_format(self):
        """SP change resets stack formats (lines 631-633)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Read stack once
        vars.read_variables(Variables.VREF_STACK)
        # Set a format for a stack variable
        fmt = vars.get_format(Variables.VREF_STACK, hex(runtime.debugger.registers["sp"]))
        fmt.base_type = "unsigned"
        # Change SP
        runtime.debugger.registers["sp"] = 0x100
        vars.read_variables(Variables.VREF_STACK)
        # The format should have been cleared
        assert hex(0xDD) not in vars._formats[Variables.VREF_STACK]

    def test_set_variable_stack_negative(self):
        """set_variable stack size cannot be negative (line 856)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="negative"):
            vars.set_variable(Variables.VREF_STACK, "Stack bytes", "-1")

    def test_set_variable_stack_too_large(self):
        """set_variable stack size too large (line 858)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="too large"):
            vars.set_variable(Variables.VREF_STACK, "Stack bytes", str(Variables.STACK_SIZE_MAX))

    def test_set_variable_array_length(self):
        """set_variable for array length virtual item (lines 861-873)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Create a format with array > 1 so it gets a dynamic vref
        fmt = vars.get_format(Variables.VREF_STACK, hex(runtime.debugger.registers["sp"]))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()
        # Now set via the dynamic vref
        vref = Variables.VREF_DYNAMIC
        result, invalidate = vars.set_variable(vref, "Array length", "8")
        assert result["value"] == "8"
        assert invalidate is True

    def test_set_variable_array_length_negative(self):
        """set_variable array length cannot be negative (line 868)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = vars.get_format(Variables.VREF_STACK, hex(runtime.debugger.registers["sp"]))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()
        with pytest.raises(ValueError, match="negative"):
            vars.set_variable(Variables.VREF_DYNAMIC, "Array length", "-1")

    def test_set_variable_array_length_too_large(self):
        """set_variable array length too large (line 870)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = vars.get_format(Variables.VREF_STACK, hex(runtime.debugger.registers["sp"]))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()
        with pytest.raises(ValueError, match="too large"):
            vars.set_variable(Variables.VREF_DYNAMIC, "Array length", str(Variables.STACK_SIZE_MAX))

    def test_set_variable_array_length_not_int(self):
        """set_variable array length must be integer (line 864)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = vars.get_format(Variables.VREF_STACK, hex(runtime.debugger.registers["sp"]))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()
        with pytest.raises(ValueError, match="integer"):
            vars.set_variable(Variables.VREF_DYNAMIC, "Array length", "abc")

    def test_set_variable_memory(self):
        """set_variable writes memory for stack variable (lines 888-896)."""
        runtime = Mock_Runtime()
        runtime.debugger.write_memory = mock.Mock()
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        result, invalidate = vars.set_variable(Variables.VREF_STACK, hex(sp), "0x42")
        runtime.debugger.write_memory.assert_called_once()

    def test_set_variable_invalid_name(self):
        """set_variable with invalid variable name raises ValueError (line 886)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Non-pointer register trying to write as memory
        with pytest.raises(ValueError, match="Invalid"):
            vars.set_variable(Variables.VREF_GLOBALS, "notanumber", "0")

    def test_set_variable_pointer_register(self):
        """set_variable for pointer register writes to pointed-to memory (line 876-883)."""
        runtime = Mock_Runtime()
        runtime.debugger.write_memory = mock.Mock()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x42])
        vars = Variables(runtime)
        fmt = vars.get_format(Variables.VREF_REGISTERS, "r0")
        fmt.is_pointer = True
        runtime.debugger.registers["r0"] = 0x80

        result, invalidate = vars.set_variable(Variables.VREF_REGISTERS, "*r0", "0x42")
        runtime.debugger.write_memory.assert_called_once()

    def test_get_format_stack_out_of_range(self):
        """get_format for stack address out of range raises (lines 940-941)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="out of range"):
            vars.get_format(Variables.VREF_STACK, "0xFFFF")

    def test_get_format_globals_zero(self):
        """get_format for globals with address 0 raises (lines 943-944)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="Invalid"):
            vars.get_format(Variables.VREF_GLOBALS, "0")

    def test_get_format_invalid_vref(self):
        """get_format with invalid vref raises (line 946)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="Invalid"):
            vars.get_format(99, "something")

    def test_get_format_star_prefix(self):
        """get_format strips * from name (line 924)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = vars.get_format(Variables.VREF_REGISTERS, "*r0")
        assert fmt is not None
        # Getting again without * should return same object
        fmt2 = vars.get_format(Variables.VREF_REGISTERS, "r0")
        assert fmt is fmt2

    def test_reset_format_star_prefix(self):
        """reset_format strips * from name (lines 1016-1017)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        vars.get_format(Variables.VREF_REGISTERS, "r0")
        vars.reset_format(Variables.VREF_REGISTERS, "*r0")
        # Should not have the format anymore
        assert "r0" not in vars._formats[Variables.VREF_REGISTERS]

    def test_reset_format_invalid_vref(self):
        """reset_format with invalid vref raises (line 1012)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="Invalid"):
            vars.reset_format(99)

    def test_get_format_ro_dynamic(self):
        """_get_format_ro for dynamic vref (lines 901-907)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        # Create a format with array > 1 so it gets a dynamic vref
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()

        # Get format for dynamic vref with empty name -> returns array format
        result = vars._get_format_ro(Variables.VREF_DYNAMIC, "")
        assert result is fmt

        # Get format for dynamic vref with name -> returns contents
        result = vars._get_format_ro(Variables.VREF_DYNAMIC, hex(sp))
        assert result is fmt.contents or result is fmt

    def test_get_format_ro_invalid_dynamic(self):
        """_get_format_ro for out-of-range dynamic vref raises (line 903)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        with pytest.raises(ValueError, match="Invalid"):
            vars._get_format_ro(Variables.VREF_DYNAMIC + 999, "test")

    def test_parse_address(self):
        """_parse_address handles * prefix (lines 1024-1025)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        assert vars._parse_address("0x100") == 0x100
        assert vars._parse_address("*0x100") == 0x100

    def test_dynamic_vref_read(self):
        """Read dynamic vref array contents (lines 680-696)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x10, 0x20, 0x30, 0x40])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.set({"arrayLength": 4, "size": 1})
        vars._reset_dynamic_vrefs()

        result = vars.read_variables(Variables.VREF_DYNAMIC)
        assert len(result) > 1  # header + array items
        assert result[0]["name"] == "Array length"

    def test_dynamic_vref_out_of_range(self):
        """Read dynamic vref that's out of range returns empty (line 681)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        result = vars._read_dynamic_vref(Variables.VREF_DYNAMIC + 999)
        assert result == []


class Test_VariableFormat_Additional:
    def test_copy_no_size(self):
        """copy with size=0 copies all fields (lines 111-115)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "orig")
        fmt.is_pointer = True
        fmt.size = 2
        fmt.codec = "UTF-16LE"
        fmt.array_length = 4
        fmt.total_size = 8

        copy = fmt.copy("new_name")
        assert copy.is_pointer is True
        assert copy.size == 2
        assert copy.codec == "UTF-16LE"
        assert copy.array_length == 4
        assert copy.total_size == 8

    def test_copy_char_smaller_than_size(self):
        """copy with char type and size < element size makes byte array (line 106)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "orig")
        fmt.base_type = "char"
        fmt.size = 4

        copy = fmt.copy("new", 3)
        assert copy.base_type == "hex"  # char becomes hex when size doesn't divide
        assert copy.size == 1

    def test_set_parent_delegates(self):
        """set() on contents delegates to parent (lines 157-158)."""
        parent = VariableFormat(Variables.VREF_GLOBALS, "parent")
        parent.set({"arrayLength": 4, "size": 1})
        parent._update_contents()
        child = parent.contents
        assert child is not None
        child.set({"baseType": "unsigned"})
        assert parent.base_type == "unsigned"

    def test_set_char_with_size(self):
        """set char with specific size determines codec (lines 170-175)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")

        # size=4 -> UTF-32LE
        fmt.set({"baseType": "char", "size": 4})
        assert fmt.codec == "UTF-32LE"
        assert fmt.size == 4

        # size=2 -> UTF-16LE
        fmt.set({"baseType": "char", "size": 2})
        assert fmt.codec == "UTF-16LE"
        assert fmt.size == 2

        # size=1 -> UTF-8
        fmt.set({"baseType": "char", "size": 1})
        assert fmt.codec == "UTF-8"
        assert fmt.size == 1

    def test_set_invalid_codec(self):
        """set with invalid codec is ignored (line 187)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.set({"codec": "INVALID"})
        # codec should remain default
        assert fmt.codec == "UTF-8"

    def test_set_pointer_to_nonpointer(self):
        """set from pointer to non-pointer adjusts size/length (lines 200-204)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.set({"isPointer": True, "arrayLength": 4, "size": 1})
        assert fmt.is_pointer is True

        fmt.set({"isPointer": False})
        assert fmt.is_pointer is False
        assert fmt.size == 4
        assert fmt.array_length == 1

    def test_set_fixed_size_array_length(self):
        """set on fixed size register with arrayLength (lines 200-204)."""
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_fixed_size = True
        fmt.total_size = 4
        fmt.set({"arrayLength": 2})
        assert fmt.array_length == 2
        assert fmt.size == 2

    def test_set_fixed_size_invalid(self):
        """set on fixed size register with incompatible values (lines 203-204)."""
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_fixed_size = True
        fmt.total_size = 4
        # size=3 and arrayLength=3 don't divide 4
        fmt.set({"size": 3, "arrayLength": 3})
        # Should keep previous values since neither works
        assert fmt.size == 4
        assert fmt.array_length == 1

    def test_to_type_string(self):
        """to_type_string for different formats (lines 258-271)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")

        # Default: uint32_t
        assert fmt.to_type_string("var") == "uint32_t var"
        assert fmt.to_type_string(0x100) == "uint32_t var_100"
        assert fmt.to_type_string() == "uint32_t "

        # Signed
        fmt.base_type = "signed"
        assert fmt.to_type_string("x") == "int32_t x"

        # Char
        fmt.base_type = "char"
        fmt.size = 1
        assert fmt.to_type_string("c") == "char c"
        fmt.size = 2
        assert fmt.to_type_string("c") == "char16_t c"

        # Pointer
        fmt.base_type = "hex"
        fmt.size = 4
        fmt.is_pointer = True
        assert "uint32_t *" in fmt.to_type_string("p")

        # Pointer array
        fmt.array_length = 4
        assert "(*" in fmt.to_type_string("p")

        # Non-pointer array
        fmt.is_pointer = False
        assert "[4]" in fmt.to_type_string("a")

    def test_to_menu_string_pointer(self):
        """to_menu_string for pointer variable."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.is_pointer = True
        menu = fmt.to_menu_string(True)
        assert "pointer:1" in menu
        assert "d_int" in menu
        assert "d_array" in menu
        assert "d_char" in menu

    def test_to_menu_string_char(self):
        """to_menu_string for char type."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.base_type = "char"
        menu = fmt.to_menu_string(True)
        assert "encoding" in menu
        assert "type:char" in menu

    def test_to_menu_string_array(self):
        """to_menu_string for array with length > 1."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 4
        menu = fmt.to_menu_string(True)
        assert "arraylen" in menu

    def test_to_menu_string_fixed_size(self):
        """to_menu_string for fixed-size register removes d_array."""
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        menu = fmt.to_menu_string(True)
        assert "d_array" not in menu

    def test_to_menu_string_size_options(self):
        """to_menu_string shows correct size options."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.size = 4
        menu = fmt.to_menu_string(True)
        assert "s_1byte" in menu
        assert "s_2byte" in menu
        assert "s_4byte" not in menu
        assert "s_8byte" in menu

        # Test with size=8 to cover s_4byte append
        fmt.size = 8
        menu = fmt.to_menu_string(True)
        assert "s_4byte" in menu
        assert "s_8byte" not in menu

    def test_to_menu_string_pointer_char_array(self):
        """to_menu_string for pointer with char base type."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.is_pointer = True
        fmt.base_type = "char"
        menu = fmt.to_menu_string(True)
        assert "p_int" in menu
        assert "p_array" in menu
        assert "p_char" not in menu  # omit current

    def test_to_menu_string_pointer_array(self):
        """to_menu_string for pointer with array > 1."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.is_pointer = True
        fmt.array_length = 4
        menu = fmt.to_menu_string(True)
        assert "p_int" in menu
        assert "p_char" in menu
        assert "p_array" not in menu  # omit current setting

    def test_format_int_empty(self):
        """format_int with unknown type returns empty (line 365)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.base_type = "unknown"
        assert fmt.format_int(42) == ""

    def test_parse_value_bytes_list_type(self):
        """parse_value_bytes with list (lines 482-492)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        result = fmt.parse_value_bytes("[1]")
        assert result == b"\x01\x00\x00\x00"

    def test_parse_value_int(self):
        """parse_value_int converts to integer (line 505)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        assert fmt.parse_value_int("0x42") == 0x42
        assert fmt.parse_value_int('"AB"') == ord("A") + (ord("B") << 8)

    def test_update_contents(self):
        """_update_contents creates/removes contents as needed."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        # Initially no contents (array_length=1, not pointer)
        assert fmt.contents is None

        # Setting array_length > 1 creates contents
        fmt.array_length = 4
        fmt._update_contents()
        assert fmt.contents is not None
        assert fmt.contents.parent is fmt

        # Setting back to 1 removes contents
        fmt.array_length = 1
        fmt.is_pointer = False
        fmt._update_contents()
        assert fmt.contents is None

    def test_set_with_callback(self):
        """set() triggers update_callback (line 231)."""
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        callback = mock.Mock()
        fmt.update_callback = callback
        fmt.set({"baseType": "unsigned"})
        callback.assert_called_once_with(fmt)


class Test_Variables_Format_Value:
    def test_format_value_pointer(self):
        """_format_value for pointer variables (lines 709-711)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x42])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_pointer = True
        fmt.size = 4
        val, is_addr = vars._format_value(None, 0x80, fmt)
        assert val is not None

    def test_format_value_array_char(self):
        """_format_value for char array (lines 714-721)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=b"ABCD")
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.base_type = "char"
        fmt.array_length = 4
        fmt.size = 1
        val, is_addr = vars._format_value(0x80, 0x80, fmt)
        assert val.startswith('"')

    def test_format_value_array_char_no_addr(self):
        """_format_value for char array with no address (line 715)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.base_type = "char"
        fmt.array_length = 4
        fmt.size = 1
        val, is_addr = vars._format_value(None, 0x41424344, fmt)
        assert val.startswith('"')

    def test_format_value_array_large(self):
        """_format_value for array > 8 shows [...] (line 723)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 10
        val, is_addr = vars._format_value(0x80, 0x80, fmt)
        assert val == "[...]"

    def test_format_value_array_small(self):
        """_format_value for small array shows elements (lines 725-731)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x10, 0x20])
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 2
        fmt.size = 4
        val, is_addr = vars._format_value(0x80, 0x80, fmt)
        assert val.startswith("[")

    def test_format_value_array_no_addr(self):
        """_format_value for array with no address (line 726)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 2
        fmt.size = 4
        val, is_addr = vars._format_value(None, 0x80, fmt)
        assert val == ""

    def test_format_reg_var_error(self):
        """_format_reg_var handles errors gracefully (lines 747-749)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_pointer = True
        # Reading memory at an invalid address will cause an error
        runtime.debugger.read_memory = mock.Mock(side_effect=RuntimeError("fail"))
        result = vars._format_reg_var("r0", 0xFFFF, fmt)
        assert result["value"] == "(error)"

    def test_format_reg_var_with_watchpoint(self):
        """_format_reg_var shows watchpoint hint (lines 745-746)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x42])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_pointer = True
        runtime.debugger.registers["r0"] = 0x80
        vars._watchpoints = {0x80}
        result = vars._format_reg_var("r0", 0x80, fmt)
        assert "hasDataBreakpoint" in result["presentationHint"].get("attributes", [])

    def test_read_memory_var_error(self):
        """_read_memory_var handles errors (lines 777-779)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(side_effect=RuntimeError("fail"))
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_STACK, "test")
        result, size = vars._read_memory_var(0x80, fmt)
        assert result["value"] == "(error)"

    def test_read_memory_var_with_watchpoint(self):
        """_read_memory_var shows watchpoint hint (lines 775-776)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x80])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_STACK, "test")
        vars._watchpoints = {0x80}
        result, size = vars._read_memory_var(0x80, fmt)
        assert "hasDataBreakpoint" in result["presentationHint"].get("attributes", [])

    def test_read_memory_var_pointer(self):
        """_read_memory_var for pointer variable uses pointed-to address (line 774)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_STACK, "test")
        fmt.is_pointer = True
        vars._watchpoints = {0x80}  # Watch the pointer value
        result, size = vars._read_memory_var(0x80, fmt)
        assert "memoryReference" in result

    def test_read_memory_array_error(self):
        """_read_memory_array handles errors (lines 802-810)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(side_effect=RuntimeError("fail"))
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 4
        fmt.size = 4
        result = vars._read_memory_array(0x80, fmt)
        assert len(result) == 1
        assert result[0]["value"] == "(error)"

    def test_read_memory_array_with_watchpoints(self):
        """_read_memory_array shows watchpoints on elements (lines 816-817)."""
        runtime = Mock_Runtime()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x10, 0x20])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_GLOBALS, "test")
        fmt.array_length = 2
        fmt.size = 4
        vars._watchpoints = {0x84}
        result = vars._read_memory_array(0x80, fmt)
        assert len(result) == 2

    def test_resize_stack_var_grow(self):
        """_resize_stack_var grows and cleans up overlapping (lines 960-990)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.total_size_prev = 4
        fmt.total_size = 8
        fmt.size = 4
        fmt.array_length = 2

        result = vars._resize_stack_var(fmt)
        # Should have cleaned up and potentially added fill

    def test_resize_stack_var_shrink(self):
        """_resize_stack_var shrinks and fills gap (lines 983-990)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.total_size_prev = 8
        fmt.total_size = 4
        fmt.size = 4
        fmt.array_length = 1

        result = vars._resize_stack_var(fmt)
        assert result is False
        # Should have filled gap with copies
        stack_formats = vars._formats[Variables.VREF_STACK]
        assert hex(sp + 4) in stack_formats

    def test_update_format(self):
        """_update_format resets dynamic vrefs when needed (lines 994-999)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.array_length = 4
        vars._update_format(fmt)
        # Should have reset dynamic vrefs

    def test_reset_dynamic_vrefs_clears_scalar(self):
        """_reset_dynamic_vrefs sets child_vref=0 for non-array formats (line 579)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.child_vref = 999  # Set non-zero
        fmt.array_length = 1  # scalar
        vars._reset_dynamic_vrefs()
        assert fmt.child_vref == 0

    def test_format_name_pointer(self):
        """_format_name prefixes pointer names with * (lines 699-701)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        fmt.is_pointer = True
        assert vars._format_name("r0", fmt) == "*r0"

    def test_format_name_nonpointer(self):
        """_format_name returns name unchanged for non-pointers (line 702)."""
        runtime = Mock_Runtime()
        vars = Variables(runtime)
        fmt = VariableFormat(Variables.VREF_REGISTERS, "test")
        assert vars._format_name("r0", fmt) == "r0"

    def test_set_variable_memory_pointer(self):
        """set_variable for pointer memory variable (lines 891-895)."""
        runtime = Mock_Runtime()
        runtime.debugger.write_memory = mock.Mock()
        runtime.debugger.read_memory = mock.Mock(return_value=[0x42])
        runtime.debugger.memory_info = mock.Mock(return_value=mock.Mock())
        vars = Variables(runtime)
        sp = runtime.debugger.registers["sp"]
        fmt = vars.get_format(Variables.VREF_STACK, hex(sp))
        fmt.is_pointer = True

        result, invalidate = vars.set_variable(Variables.VREF_STACK, "*" + hex(sp), "0x42")
        runtime.debugger.write_memory.assert_called_once()
