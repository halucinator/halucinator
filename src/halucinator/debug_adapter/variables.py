from __future__ import annotations

import re
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Match,
    Optional,
    Set,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from .debug_adapter import HalRuntime


# Supported character encodings
_CODEC_SIZES = {
    "UTF-8": 1,
    "UTF-16LE": 2,
    "UTF-16BE": 2,
    "UTF-32LE": 4,
    "UTF-32BE": 4,
}

# Regex for VariableFormat.quote_string
_QUOTE_STRING_RE = re.compile(r"(\\\\|\\x..|\\u....|\\U........)")

# Regex for VariableFormat.parse_value
_PARSE_VALUE_RE = re.compile(r"(\\\\|\\x[0-9A-Fa-f]{2})")

# Two's complement conversion
def _int_to_signed(val: int, size: int) -> int:
    max_int = (1 << (size * 8)) - 1
    if val <= (max_int >> 1) or val > max_int:
        return val
    return -(max_int - val + 1)


class VariableFormat(object):
    """ Formatting for a single variable.

    Each variable may represent either a simple scalar value, a pointer to
    a memory location, or an array of values. Each element may be formatted
    as an integer in either base-16 (hex) or base-10 (signed or unsigned) or
    as a character, and may have the size adjusted by the user.
    """

    def __init__(self, vref: int, name: str) -> None:
        self.is_pointer = False
        self.size = 4
        self.array_length = 1
        self.base_type = "hex"
        self.codec = "UTF-8"
        self.vref = vref
        self.name = name
        # "contents" represents the format of each element of this array
        self.contents: Optional["VariableFormat"] = None
        # "parent" represents the containing array format
        self.parent: Optional["VariableFormat"] = None
        self.total_size = self.size * self.array_length
        self.total_size_prev = self.total_size
        self.child_vref = 0
        self.is_fixed_size = vref == Variables.VREF_REGISTERS
        self.update_callback: Optional[
            Callable[["VariableFormat"], None]
        ] = None

        if vref == Variables.VREF_REGISTERS:
            self.section = "reg"
        elif vref == Variables.VREF_STACK:
            self.section = "stack"
        elif vref == Variables.VREF_GLOBALS:
            self.section = "global"
        else:
            self.section = "array"

    def copy(self, name: str = "", size: int = 0) -> "VariableFormat":
        """ Creates a new VariableFormat based on this one

        Parameters:
            name: The name of the new variable.
            size: The expected total size of the new format.
                The array length and element size are adjusted as needed to
                exactly match the requested size.
        """
        copy = VariableFormat(self.vref, name)
        copy.base_type = self.base_type
        copy.update_callback = self.update_callback
        if size != 0:
            if self.is_pointer:
                copy.base_type = "hex"

            if size >= self.size and size % self.size == 0:
                # Make an array with the same element size if we can
                copy.size = self.size
                copy.codec = self.codec
            else:
                # Make a byte array to keep things aligned
                copy.size = 1
                if self.base_type == "char":
                    copy.base_type = "hex"

            copy.array_length = size // copy.size
            copy.total_size = size
        else:
            copy.is_pointer = self.is_pointer
            copy.size = self.size
            copy.codec = self.codec
            copy.array_length = self.array_length
            copy.total_size = self.total_size
        copy._update_contents()
        return copy

    # Update the child "contents" to match what it should be.
    # This field represents the format of each array element.
    def _update_contents(self) -> None:
        if not self.is_pointer and self.array_length == 1:
            self.contents = None
            return

        if self.contents is None:
            self.contents = VariableFormat(-1, self.name)
            self.contents.parent = self
            # Keeping defaults: is_pointer=False, array_length=1

        contents = self.contents
        contents.base_type = self.base_type
        contents.codec = self.codec
        contents.size = self.size
        contents.total_size = self.size

    def set(self, details: Dict[str, Any]) -> None:
        """ Modifies this format based on the provided details

        Parameters:
            details: dict
                Contains one or more options describing how this format should
                be changed. May have the following keys:

                isPointer: bool
                size: int
                    Number of bytes per word; must be one of 1, 2, 4, or 8
                arrayLength: int
                    Number of words in array; must be 1 or greater
                baseType: str
                    Must be one of "hex", "signed", "unsigned", "char"
                codec: str
                    Must be one of "UTF-8", "UTF-16LE", "UTF-16BE",
                    "UTF-32LE", "UTF-32BE"
        """
        if self.parent is not None:
            self.parent.set(details)
            return

        is_pointer = details.get("isPointer", self.is_pointer)
        size = details.get("size")
        array_length = details.get("arrayLength")
        base_type = details.get("baseType")
        codec = details.get("codec")

        # Default char encoding to 1 byte (UTF8)
        if base_type == "char" and codec is None:
            if size is not None:
                # Determine codec from size; assume little-endian UTF
                if size == 4:
                    codec = "UTF-32LE"
                elif size == 2:
                    codec = "UTF-16LE"
                elif size == 1:
                    codec = "UTF-8"
            elif self.base_type != "char":
                # Default to UTF-8 when changing from non-char to char
                codec = "UTF-8"
                size = 1

        # Adjust size to match codec
        if codec is not None:
            codec_size = _CODEC_SIZES.get(codec)
            if codec_size is not None:
                size = codec_size
            else:
                codec = None

        # Adjust size+length when changing type from pointer to non-pointer
        if self.is_pointer and not is_pointer:
            if size is None:
                size = 4
            if array_length is None:
                array_length = 1

        # Registers have a fixed size of 4 bytes
        if self.is_fixed_size and not is_pointer:
            if size in [1, 2, 4]:
                array_length = self.total_size // size
            elif array_length in [1, 2, 4]:
                size = self.total_size // array_length
            else:
                size = None
                array_length = None

        # Update details that directly correspond to inputs
        if type(is_pointer) == bool:
            self.is_pointer = is_pointer
        if size in [1, 2, 4, 8]:
            self.size = size
        if type(array_length) == int and array_length >= 1:
            self.array_length = array_length
        if base_type in ["hex", "signed", "unsigned", "char"]:
            self.base_type = base_type
        if codec is not None:
            self.codec = codec

        # Populate extra fields that can be determined automatically

        # Hide the array submenu for non-arrays
        if self.array_length == 1:
            self.child_vref = 0

        self.total_size_prev = self.total_size
        self.total_size = self.size * self.array_length
        if self.is_pointer:
            self.total_size = 4
        self._update_contents()

        if self.update_callback is not None:
            self.update_callback(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "isPointer": self.is_pointer,
            "isFixedSize": self.is_fixed_size,
            "size": self.size,
            "arrayLength": self.array_length,
            "baseType": self.base_type,
            "codec": self.codec,
            "totalSize": self.total_size,
        }

    def to_type_string(self, addr: Union[int, str, None] = None) -> str:
        """ Generate and return a C-like type string for this format

        Parameters:
            addr: The address or register name of this variable
        """
        name = ""
        if type(addr) == str:
            name = addr
        elif type(addr) == int:
            name = "var_%x" % addr

        if self.base_type == "char":
            base = "char" if self.size == 1 else "char%d_t" % (self.size * 8)
        elif self.base_type == "signed":
            base = "int%d_t" % (self.size * 8)
        else:
            base = "uint%d_t" % (self.size * 8)

        if self.is_pointer and self.array_length > 1:
            return "%s (*%s)[%d]" % (base, name, self.array_length)
        elif self.array_length > 1:
            return "%s %s[%d]" % (base, name, self.array_length)
        elif self.is_pointer:
            return "%s *%s" % (base, name)
        else:
            return "%s %s" % (base, name)

    def to_menu_string(self, is_valid_address: bool = True) -> str:
        """ Generate a menu string representing relevant VSCode menu options

        This is needed so that VSCode can hide formatting options that aren't
        relevant, like the text encoding for an integer type. Unfortunately,
        because the VSCode "when" expressions are very limited such that the
        inclusion of a menu option must be representable as matching one regex
        with no support for inverting the match or using multiple regex matches
        in parentheses, we effectively have to list every option that should be
        shown in the right-click menu.
        """
        options = []
        if is_valid_address:
            options.append("pointer")
        if self.base_type != "char":
            options.append("intformat")
        if self.is_pointer or self.vref == Variables.VREF_STACK:
            options.append("add_global")
        for intfmt in ["hex", "signed", "unsigned"]:
            if self.base_type != intfmt:
                options.append("i_" + intfmt)
        if self.array_length > 1:
            options.append("arraylen")
        if self.base_type == "char":
            options.append("encoding")
        else:
            options.append("size")

        # Non-pointer formatting options; omit the current setting
        if self.is_pointer:
            options += ["d_int", "d_array", "d_char"]
        elif self.base_type == "char":
            options += ["d_int", "d_array"]
        elif self.array_length > 1:
            options += ["d_int", "d_char"]
        else:
            options += ["d_array", "d_char"]
        # Omit the integer array option for registers
        if self.is_fixed_size:
            options.remove("d_array")

        # Pointer formatting options; omit the current setting
        if not self.is_pointer:
            options += ["p_int", "p_array", "p_char"]
        elif self.base_type == "char":
            options += ["p_int", "p_array"]
        elif self.array_length > 1:
            options += ["p_int", "p_char"]
        else:
            options += ["p_array", "p_char"]

        # Data size formatting options; omit the current size
        if self.size != 1:
            options.append("s_1byte")
        if self.size != 2:
            options.append("s_2byte")
        if self.size != 4:
            options.append("s_4byte")
        if self.size != 8:
            options.append("s_8byte")

        is_mem = self.vref != Variables.VREF_REGISTERS or self.is_pointer

        return (
            "data;"
            f"mem:{int(is_mem)};"
            f"options:{','.join(options)};"
            f"section:{self.section};"
            f"size:{self.size};"
            f"arrayLen:{self.array_length};"
            f"pointer:{int(self.is_pointer)};"
            f"type:{self.base_type}"
        )

    def format_int(self, val: int) -> str:
        """ Create a human-readable string for the provided scalar/integer

        Parameters:
            val: The value which should be formatted.
                Depending on the type of this VariableFormat, it may be
                interpreted as an unsigned integer, a two's complement
                representation of a signed integer, or a character code.
                For UTF-8, this represents a byte value, NOT a code point.
        """
        if self.base_type == "hex":
            return hex(val)
        elif self.base_type == "unsigned":
            return str(val)
        elif self.base_type == "signed":
            return str(_int_to_signed(val, self.size))
        elif self.base_type == "char":
            return self.quote_string(val.to_bytes(self.size, "little"))
        return ""

    # Enclose a string in quotes, backslash-escape nonprintable/backslash/quote
    def quote_string(self, val: bytes) -> str:
        """ Create a human-readable quoted string for the provided bytes

        The text encoding of this VariableFormat is used to decode the byte
        string. Backslash escape sequences will be returned where appropriate.
        ASCII whitespace will be represented with "\\t", "\\n", or "\\r".
        Backslash and quote characters are represented as "\\\\" and "\\\"".
        Other ASCII control codes (00-7F) and any bytes that could not decode
        as valid UTF-8 (80-FF) will be represented in hex like "\\x1f".
        Unicode whitespace and control codes that fall outside of Basic Latin
        (ASCII) will be represented as Unicode codepoints like "\\u0085",
        "\\u200e", or "\\U000e0001".

        Each of the above example sequences have only one backslash.

        Parameters:
            val: An arbitrary byte string
        """

        def repl(match: Match[str]) -> str:
            group = match.group(0)
            if group == r"\\":
                # Remove the extra backslash, which was caused by combining
                # backslashreplace and unicode_escape.
                # Note that re.sub only uses non-overlapping matches, so matching
                # this double-blackslash helps avoid interpreting a literal
                # backslash byte in the input as part of an escape sequence.
                return "\\"
            codepoint = int(group[2:], 16)
            if chr(codepoint).isprintable():
                # Unescape printable characters
                return group.encode("utf-8").decode("unicode_escape")
            elif codepoint in range(0x80, 0x100):
                # Format all non-ASCII characters with at least 4 hex digits to
                # avoid ambiguity with invalid UTF-8 sequences.
                return r"\u%04x" % codepoint
            # Leave control characters escaped, including \t, \n, and \r
            return group

        # Any bytes that don't decode as valid UTF-8 will be represented like \xff
        val = val.replace(b"\\", b"\\\\")
        decoded = val.decode(self.codec, "backslashreplace")

        # "unicode_escape" escapes all nonprintable, non-ASCII, or backslash
        # characters.
        escaped = decoded.encode("unicode_escape").decode("latin_1")

        # Use regex to unescape printable Unicode characters
        escaped = _QUOTE_STRING_RE.sub(repl, escaped).replace('"', r"\"")
        return '"%s"' % escaped

    def _parse_value_string(self, val: str) -> bytes:
        def repl(match: Match[str]) -> str:
            group = match.group(0)
            if group == r"\\":
                # Keep double-backslash as-is.
                return group
            codepoint = int(group[2:], 16)
            if group[:2] == r"\x" and codepoint >= 0x80:
                # Use surrogate characters to encode this byte directly.
                # Python's "surrogateescape" will translate codepoints like \uDCFF
                # to the byte \xFF when encoding.
                # Without this, UTF-8 would translate "\xff" into b"\xc3\xbf"
                return chr(0xDC00 + codepoint)
            return group

        if self.codec == "UTF-8":
            # Handle "\x" escapes special for UTF-8.
            val = _PARSE_VALUE_RE.sub(repl, val)

        # Interpret string escapes like "\n" or "\u00e9"
        val = val.encode("raw_unicode_escape").decode("unicode_escape")
        return val.encode(self.codec, "surrogateescape")

    def parse_value(self, val: str) -> Union[int, bytes, List[int]]:
        """ Parses a user-provided string.

        If the value is enclosed in either single or double quotes, the
        contents are parsed as a string which should have backslash escapes
        interpreted. Any standard Python backslash escape sequence should be
        accepted; this includes every sequence that may be returned by
        quoted_string as well as some others like octal. The result is then
        encoded with the current character encoding and returned as bytes.

        If the value is enclosed in square brackets, the contents are parsed
        as an integer array and a list of int is returned. Otherwise, the
        value is parsed as a single integer.
        """
        try:
            if val[0] in ['"', "'"] and val[0] == val[-1]:
                return self._parse_value_string(val[1:-1])
            if val.startswith("[") and val.endswith("]"):
                # Parse as an integer list
                return [int(v, 0) for v in val[1:-1].split(",")]
            return int(val, 0)
        except ValueError:
            raise ValueError("Unable to parse expression")

    def parse_value_bytes(self, val: str) -> bytes:
        """ Parses a user-provided string as bytes.

        The value is parsed in the same way as described for parse_value,
        but the result is converted to bytes in every case, based on the
        size and array length of this format. A ValueError is raised if the
        parsed result is too large to fit in this variable.
        """
        max_size = self.size * self.array_length
        parsed = self.parse_value(val)
        if type(parsed) == int:
            return parsed.to_bytes(max_size, "little", signed=parsed < 0)
        elif type(parsed) == bytes:
            if len(parsed) > max_size:
                raise ValueError("Value is too large")
            return parsed + bytes(max_size - len(parsed))
        elif type(parsed) == list:
            try:
                barray = bytearray()
                for e in parsed:
                    barray += e.to_bytes(self.size, "little", signed=e < 0)
                if len(barray) > max_size:
                    raise ValueError("Value is too large")
                barray += bytes(max_size - len(barray))
                return bytes(barray)
            except:
                raise ValueError("Array element is too large")
        else:
            raise ValueError("Unexpected parse type")

    def parse_value_int(self, val: str) -> int:
        """ Parses a user-provided string as an integer.

        The value is parsed in the same way as described for parse_value,
        but the result is converted to an unsigned integer. A ValueError is
        raised if the parsed result is too large to fit in this variable.
        This is most useful for registers, allowing not just integer literals
        but also short strings like "READ" to be written to a register.
        """
        return int.from_bytes(self.parse_value_bytes(val), "little")


class Variables(object):
    """ Management related to the VSCode Variables pane

    We populate three scopes (collapsible sections) of variables:
        1. Registers: shows all general-purpose CPU registers
        2. Stack: shows memory starting at the current value of SP
        3. Globals: shows arbitrary memory locations requested by the user

    All data is by default interpreted as 32-bit little endian integers and
    displayed as hex. The user can change the display format of each item to
    base-10 as signed or unsigned, as an array, or as a character string.

    Because we do not currently detect stack frames, the stack is by default
    limited to 32 bytes. The user can change this size.
    """

    VREF_REGISTERS = 1
    VREF_STACK = 2
    VREF_GLOBALS = 3
    VREF_RANGE = range(1, 4)
    VREF_DYNAMIC = 100
    STACK_SIZE_MAX = 16 * 1024

    def __init__(self, runtime: "HalRuntime") -> None:
        self._runtime = runtime
        self.stack_size = 32
        self._formats: Dict[int, Dict[str, VariableFormat]] = {
            Variables.VREF_REGISTERS: {},
            Variables.VREF_STACK: {},
            Variables.VREF_GLOBALS: {},
        }
        self._watchpoints: Set[int] = set()
        self.prev_sp = 0
        self.reg_names = {k for k in runtime.debugger.list_all_regs_names()}
        self.globals: Set[int] = set()
        self.dynamic_vrefs: List[Tuple[int, str, VariableFormat]] = []

    def get_scopes(self) -> List[Dict[str, Any]]:
        """ Returns the "scopes" list that is expected by DAP. """
        return [
            {
                "name": "Registers",
                "presentationHint": "registers",
                "variablesReference": Variables.VREF_REGISTERS,
                "expensive": False,
            },
            {
                "name": "Stack",
                "variablesReference": Variables.VREF_STACK,
                "expensive": False,
            },
            {
                "name": "Globals",
                "variablesReference": Variables.VREF_GLOBALS,
                "expensive": False,
            },
        ]

    # Regenerate the dynamic VREFs for arrays
    def _reset_dynamic_vrefs(self) -> None:
        cur_vref = Variables.VREF_DYNAMIC
        self.dynamic_vrefs.clear()
        for section in Variables.VREF_RANGE:
            for name, format in self._formats[section].items():
                if format.array_length > 1:
                    format.child_vref = cur_vref
                    if format.contents is not None:
                        format.contents.vref = cur_vref
                    self.dynamic_vrefs.append((section, name, format))
                    cur_vref += 1
                else:
                    format.child_vref = 0

    def read_variables(self, vref: int) -> List[Dict[str, Any]]:
        """ Returns the "variables" list for a single section or array.

        The result is a list of dicts in the format expected by DAP in response
        to a "variables" request. Array types return a variablesReference that
        can be passed back into this function to retrieve the contents of that
        array, which VSCode's GUI uses to populate a collapsible sublist.

        Parameters:
            vref: A DAP VariablesReference for a top-level section or an array
        """
        # Update the watchpoints list beforehand; this is needed to populate
        # the "hasDataBreakpoint" attribute in the result.
        self._watchpoints = set(
            self._runtime.debugger.list_watchpoints().values()
        )

        if vref == Variables.VREF_REGISTERS:
            return self._read_registers()
        elif vref == Variables.VREF_STACK:
            return self._read_stack()
        elif vref == Variables.VREF_GLOBALS:
            return self._read_globals()
        elif vref >= Variables.VREF_DYNAMIC:
            return self._read_dynamic_vref(vref)

        return []

    # Return all variables in the registers section
    def _read_registers(self) -> List[Dict[str, Any]]:
        vars_list: List[Dict[str, Any]] = []
        defaults = VariableFormat(Variables.VREF_REGISTERS, "")
        formats = self._formats[Variables.VREF_REGISTERS]

        regs_dict = self._runtime.getRegisters()
        vars_list = []
        for reg, val in regs_dict.items():
            format = formats.get(reg, defaults)
            details = self._format_reg_var(reg, val, format)
            vars_list.append(details)

        return vars_list

    # Return all variables in the stack section
    def _read_stack(self) -> List[Dict[str, Any]]:
        defaults = VariableFormat(Variables.VREF_STACK, "")
        formats = self._formats[Variables.VREF_STACK]

        sp = self._runtime.debugger.read_register("sp", False)

        # Reset formatting if the stack pointer changed
        if sp != self.prev_sp:
            formats.clear()
            self.prev_sp = sp

        vars_list = [
            {
                "name": "Stack bytes",
                "value": str(self.stack_size),
                "presentationHint": {"kind": "virtual"},
                "variablesReference": 0,
                "__vscodeVariableMenuContext": "header:stack",
            }
        ]
        addr = sp
        addr_end = sp + self.stack_size
        while addr < addr_end:
            format = formats.get(hex(addr), defaults)
            details, size = self._read_memory_var(addr, format)
            vars_list.append(details)
            addr += size

        return vars_list

    # Return all variables in the globals section
    def _read_globals(self) -> List[Dict[str, Any]]:
        defaults = VariableFormat(Variables.VREF_GLOBALS, "")
        formats = self._formats[Variables.VREF_GLOBALS]
        vars_list = [
            {
                "name": "Globals",
                "value": str(len(self.globals)),
                "presentationHint": {
                    "kind": "virtual",
                    "attributes": ["readOnly"],
                },
                "variablesReference": 0,
                "__vscodeVariableMenuContext": "header:global",
            }
        ]
        for addr in sorted(self.globals):
            format = formats.get(hex(addr), defaults)
            details, _ = self._read_memory_var(addr, format)
            vars_list.append(details)

        return vars_list

    # Return all variables in a dynamic array section
    def _read_dynamic_vref(self, vref: int) -> List[Dict[str, Any]]:
        vref_index = vref - Variables.VREF_DYNAMIC
        if vref_index >= len(self.dynamic_vrefs):
            return []
        section, name, format = self.dynamic_vrefs[vref_index]
        addr_opt = self.get_address(section, name, format.is_pointer)
        vars_list = [
            {
                "name": "Array length",
                "value": str(format.array_length),
                "presentationHint": {"kind": "virtual"},
                "variablesReference": 0,
            }
        ]
        if addr_opt is not None:
            vars_list += self._read_memory_array(addr_opt, format)

        return vars_list

    def _format_name(self, name: str, format: VariableFormat) -> str:
        if format.is_pointer:
            # We display the pointed-to value for this, so prefix the name
            return "*" + name
        return name

    # Create a string describing the given variable address and value
    def _format_value(
        self, addr: Optional[int], val: int, format: VariableFormat
    ) -> Tuple[str, bool]:
        if format.is_pointer:
            size = format.size
            addr = val
            val = self._runtime.debugger.read_memory(val, size)[0]

        if format.array_length > 1 and format.base_type == "char":
            if addr is None:
                val_bytes = val.to_bytes(4, "little")
            else:
                val_bytes = self._runtime.debugger.read_memory(
                    addr, 1, format.size * format.array_length, True
                )
            # Strip trailing zeroes before quoting
            return format.quote_string(val_bytes.rstrip(b"\0")), False
        if format.array_length > 8:
            return "[...]", False
        elif format.array_length > 1:
            if addr is None:
                return "", False
            words = self._runtime.debugger.read_memory(
                addr, format.size, format.array_length
            )
            word_strings = [format.format_int(w) for w in words]
            return "[%s]" % ", ".join(word_strings), False

        mrange = self._runtime.debugger.memory_info(val)
        return format.format_int(val), mrange is not None

    # Returns the DAP "variables" result dict for a single register variable
    def _format_reg_var(
        self, name: str, val: int, format: VariableFormat
    ) -> Dict[str, Any]:
        name = self._format_name(name, format)
        hint = {}
        is_addr = False
        try:
            result, is_addr = self._format_value(None, val, format)
            if format.is_pointer and val in self._watchpoints:
                hint["attributes"] = ["hasDataBreakpoint"]
        except:
            result = "(error)"
            hint["attributes"] = ["readOnly"]
        details = {
            "name": name,
            "type": format.to_type_string(name.lstrip("*")),
            "value": result,
            "variablesReference": format.child_vref,
            "presentationHint": hint,
            "__vscodeVariableMenuContext": format.to_menu_string(is_addr),
        }
        if format.is_pointer:
            details["memoryReference"] = hex(val)
        return details

    # Returns the DAP "variables" result dict for a single memory variable
    def _read_memory_var(
        self, addr: int, format: VariableFormat
    ) -> Tuple[Dict[str, Any], int]:
        size = format.size
        name = self._format_name(hex(addr), format)
        hint = {}
        memref = addr
        is_addr = False
        try:
            val_int = self._runtime.debugger.read_memory(addr, size, 1)[0]
            value, is_addr = self._format_value(addr, val_int, format)
            memref = val_int if format.is_pointer else addr
            if memref in self._watchpoints:
                hint["attributes"] = ["hasDataBreakpoint"]
        except:
            value = "(error)"
            hint["attributes"] = ["readOnly"]
        return (
            {
                "name": name,
                "type": format.to_type_string(addr),
                "value": value,
                "variablesReference": format.child_vref,
                "presentationHint": hint,
                "memoryReference": hex(memref),
                "__vscodeVariableMenuContext": format.to_menu_string(is_addr),
            },
            format.total_size,
        )

    # Returns the DAP "variables" result dict for every item in an array
    def _read_memory_array(
        self, addr: int, format: VariableFormat
    ) -> List[Dict[str, Any]]:
        item_format = format.contents or format
        try:
            words = self._runtime.debugger.read_memory(
                addr, format.size, format.array_length
            )
        except:
            return [
                {
                    "name": hex(addr),
                    "value": "(error)",
                    "variablesReference": 0,
                    "presentationHint": {"attributes": ["readOnly"]},
                }
            ]
        result = []
        for idx, word in enumerate(words):
            cur_addr = addr + idx * format.size
            value, _ = self._format_value(cur_addr, word, item_format)
            hint = {}
            if cur_addr in self._watchpoints:
                hint["attributes"] = ["hasDataBreakpoint"]
            result.append(
                {
                    "name": hex(cur_addr),
                    "type": item_format.to_type_string(cur_addr),
                    "value": value,
                    "variablesReference": 0,
                    "memoryReference": hex(cur_addr),
                }
            )
        return result

    def set_variable(
        self, vref: int, name: str, val: str
    ) -> Tuple[Dict[str, Any], bool]:
        """ Handling for a user request to set any variable

        For special virtual "variable" items, this function can let the user
        set the size of a stack or array. For everything else, it accepts an
        input string which may either describe a string (wrapped in quotes),
        an array of integers, or a single integer, with each integer denoted
        either in base 10 (with optional negative sign) or base-16 with a "0x"
        prefix.

        Parameters:
            vref: The DAP variablesReference.
                Denotes which section of the variables pane this variable
                belongs to, or which array this item is nested under.
            name: The name that was displayed for this variable in the GUI
            val: The string that the user is attempting to set the value to
        """
        # Special handling for virtual header items
        if vref == Variables.VREF_STACK and name.startswith("Stack"):
            # Stack size
            try:
                val_parsed = int(val, 0)
            except ValueError:
                raise ValueError("Stack size must be an integer")
            if val_parsed < 0:
                raise ValueError("Stack size cannot be negative")
            if val_parsed >= Variables.STACK_SIZE_MAX:
                raise ValueError("Stack size is too large")
            self.stack_size = val_parsed
            return {"value": str(val_parsed)}, True
        elif vref >= Variables.VREF_DYNAMIC and name.startswith("Array"):
            # Array length
            try:
                val_parsed = int(val, 0)
            except ValueError:
                raise ValueError("Array length must be an integer")
            if val_parsed < 0:
                raise ValueError("Array length cannot be negative")
            if val_parsed >= Variables.STACK_SIZE_MAX:
                raise ValueError("Array length is too large")
            format = self.get_format(vref, "")
            format.array_length = val_parsed
            return {"value": str(val_parsed)}, True

        format = self.get_format(vref, name)
        addr = self.get_address(vref, name, format.is_pointer)

        # Handle writing a register value (for non-pointer only)
        if vref == Variables.VREF_REGISTERS and addr is None:
            val_int = format.parse_value_int(val)
            self._runtime.setRegister(name, val_int)
            result, is_addr = self._format_value(None, val_int, format)
            return {"value": result}, is_addr

        if addr is None:
            raise ValueError("Invalid variable name")

        # Handle writing a memory variable
        val_bytes = format.parse_value_bytes(val)
        self._runtime.debugger.write_memory(addr, 1, val_bytes, 1, True)
        val_int = addr
        if not format.is_pointer:
            val_int = int.from_bytes(val_bytes[0 : format.size], "little")
        result, _ = self._format_value(addr, val_int, format)
        invalidate = format.contents is not None or format.parent is not None
        return {"value": result}, invalidate

    # Get a format that has already been populated
    def _get_format_ro(self, vref: int, name: str) -> Optional[VariableFormat]:
        if vref >= Variables.VREF_DYNAMIC:
            vref_index = vref - Variables.VREF_DYNAMIC
            if vref_index >= len(self.dynamic_vrefs):
                raise ValueError("Invalid variable reference")
            array_format = self.dynamic_vrefs[vref_index][2]
            if name != "":
                return array_format.contents or array_format
            return array_format

        format_vars = self._formats.get(vref, {})
        return format_vars.get(name)

    def get_format(self, vref: int, name: str) -> VariableFormat:
        """ Obtains a VariableFormat for the described variable.

        If no matching VariableFormat already exists, a new one is created and
        returned, and the same object will be returned for any future calls to
        this function until it is removed, such as by reset_format.

        Parameters:
            vref: The DAP variablesReference for this variable
            name: The variable name
        """
        if name.startswith("*"):
            name = name[1:]
        format = self._get_format_ro(vref, name)

        if format is None:
            try:
                addr = int(name, 0)
            except ValueError:
                addr = 0

            # Verify that the variable is valid
            if vref == Variables.VREF_REGISTERS:
                if name not in self.reg_names:
                    raise ValueError("Invalid register name provided")
            elif vref == Variables.VREF_STACK:
                sp_value = self._runtime.debugger.read_register("sp", False)
                offset = addr - sp_value
                if offset < 0 or offset >= self.stack_size:
                    raise ValueError("Stack address is out of range")
            elif vref == Variables.VREF_GLOBALS:
                if addr == 0:
                    raise ValueError("Invalid address")
            else:
                raise ValueError("Invalid variable reference")

            # Create and store a new VariableFormat
            format = VariableFormat(vref, name)
            format.update_callback = self._update_format
            self._formats[vref][name] = format

        return format

    # Automatically resize the stack to accomodate changes that have been made
    # to the given variable format. New VariableFormat objects will be created
    # to fill any gaps in the stack, while existing overlapping ones will be
    # removed.
    def _resize_stack_var(self, format: VariableFormat) -> bool:
        addr_start = int(format.name, 0)
        new_size = format.total_size
        old_size = format.total_size_prev
        new_end = addr_start + new_size
        old_end = addr_start + old_size
        stack_formats = self._formats[Variables.VREF_STACK]
        if new_size > old_size:
            # Clear the formatting of overlapping variables
            for addr in range(old_end, new_end):
                stack_formats.pop(hex(addr), None)
            # Add bytes to keep stack aligned to 4 bytes
            cleanup_end = new_end
            if cleanup_end % 4 != 0:
                cleanup_end += 4 - cleanup_end % 4
            for addr in range(new_end, cleanup_end):
                if hex(addr) in stack_formats:
                    cleanup_end = addr
                    break
            if cleanup_end > new_end:
                name = hex(new_end)
                new_format = format.copy(name, cleanup_end - new_end)
                stack_formats[name] = new_format
                return True
        if new_size < old_size:
            # Copy this format (as non-array) to fill the old size
            element_size = 4 if format.is_pointer else format.size
            for addr in range(new_end, old_end, element_size):
                name = hex(addr)
                new_format = format.copy(name, element_size)
                stack_formats[name] = new_format
        return False

    # Finalize changes for a variable's VariableFormat; clean up stack/vrefs
    def _update_format(self, format: VariableFormat) -> None:
        reset_vrefs = format.array_length > 1
        if format.vref == Variables.VREF_STACK:
            reset_vrefs |= self._resize_stack_var(format)

        if reset_vrefs:
            self._reset_dynamic_vrefs()

    def reset_format(self, vref: int, name: Optional[str] = None) -> None:
        """ Handle a user request to reset variable formats to default

        Parameters:
            vref: The DAP variablesReference denoting a main section.
                Array vrefs are not supported by this function.
            name: A variable name. If omitted, every variable in this section
                will be reset.
        """
        format_vars = self._formats.get(vref)
        if format_vars is None:
            raise ValueError("Invalid variable reference")
        if name is None:
            format_vars.clear()
        else:
            if name.startswith("*"):
                name = name[1:]
            format_vars.pop(name, None)

    # Parse a memory variable's name as the address integer.
    # For pointer variables, this returns the address of the variable itself,
    # not of the pointed-to data.
    def _parse_address(self, addr_string: str) -> int:
        if addr_string.startswith("*"):
            addr_string = addr_string[1:]
        return int(addr_string, 0)

    def remove_globals(self, global_list: List[str]) -> None:
        """ Removes one or more global variables by name

        Parameters:
            global_list: A list of strings, each of which should be the name of
                a memory variable that was provided by `read_variables()`.
        """
        global_formats = self._formats[Variables.VREF_GLOBALS]
        global_addrs = [self._parse_address(s) for s in global_list]
        global_names = [str(a) for a in global_addrs]
        self.globals -= set(global_addrs)
        for name in global_names & global_formats.keys():
            del global_formats[name]

    def add_globals(self, global_list: List[str]) -> None:
        """ Add one or more global variables by name

        Parameters:
            global_list: A list of strings, each of which should be the name of
                a memory variable that was provided by `read_variables()`.
        """
        global_addrs = [self._parse_address(s) for s in global_list]
        self.globals |= set(global_addrs)

    def get_address(
        self, vref: int, name: str, deref: Optional[bool] = None
    ) -> Optional[int]:
        """ Returns an address for the specified variable.

        Returns None for non-pointer register variables.

        Parameters:
            vref: The DAP variablesReference for a section or array
            name: A variable name that was returned by `read_variables()`
            deref: If True, the address of this variable's pointed-to data
                is returned instead of the variable's own address.
                When None or unspecified, the behavior depends on whether
                the specified variable is a pointer type.
        """
        if name.startswith("*"):
            name = name[1:]
            if deref is None:
                deref = True

        if deref is None:
            try:
                format = self._get_format_ro(vref, name)
                deref = format.is_pointer if format else False
            except ValueError:
                pass

        if vref == Variables.VREF_REGISTERS:
            if deref:
                addr = self._runtime.getRegisters().get(name)
                return addr
            else:
                return None

        try:
            addr = int(name, 0)
        except ValueError:
            return None

        if deref:
            addr = self._runtime.debugger.read_memory(addr, 4)[0]

        return addr
