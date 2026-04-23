# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
from __future__ import annotations

"""
Implements the BPHandlers class, bp_handle decorator, and other helpers for bp_handlers
"""

import struct
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple, Union

if TYPE_CHECKING:
    from halucinator.qemu_targets.hal_qemu import HALQemuTarget

HandlerReturn = Tuple[bool, Optional[int]]
# The commented-out version of this definition should be correct, but
# we can't use it until the actual uses of @bp_handler are typed,
# otherwise the function type being passed to it don't match.
HandlerFunction = Callable[..., HandlerReturn]
# HandlerFunction = Callable[[Any, HALQemuTarget, int], HandlerReturn]
HandlerDecorator = Callable[[HandlerFunction], HandlerFunction]


def bp_handler(
    arg: Union[HandlerFunction, List[str]]
) -> Union[HandlerFunction, HandlerDecorator]:
    """
    @bp_handler decorator

    arg: either the function if used as @bp_handler
        or a list of intercepting functions e.g., @bp_handler(['F1','F2'])

    """
    if callable(arg):
        # Handles @bp_handler with out args allows any function
        arg.is_bp_handler = True  # type: ignore
        return arg

    # Handles @bp_handler(['F1','F2'])
    def bp_decorator(func: HandlerFunction) -> HandlerFunction:
        func.bp_func_list = arg  # type: ignore
        return func

    return bp_decorator


class BPHandler:  # pylint: disable=too-few-public-methods
    """
    Base Class to implement custom BP intercepts from
    """

    def register_handler(
        self, qemu: HALQemuTarget, addr: int, func_name: str
    ) -> HandlerFunction:  # pylint: disable=unused-argument
        """
        Determines what method should used to handle the break point address

        This method uses the list added to methods using the bp_handle decorator
        to determine the method and returns the first match it finds
        """
        canidate_methods = [
            getattr(self.__class__, x)
            for x in dir(self.__class__)
            if hasattr(getattr(self.__class__, x), "bp_func_list")
        ]
        for canidate in canidate_methods:
            if func_name in canidate.bp_func_list:
                return canidate

        error_str = (
            f"{self.__class__.__name__} does not have bp_handler for {func_name}"
        )
        raise ValueError(error_str)


class BPStruct:
    """
    This creates a class that will defining a structure and allow using the `.` notation
    to access its members for use with in BP Handlers
    Note requires python>3.6 so **kwargs is ordered

    To create pass names of struct members with a python package struct strings
    as parameters

    example:
        struct example{
            uint32_t  le_32;      // Little Endian 32 bit unsigned int
            uint16_t  be_int16;   // Big Endian 16 bit int
            uint64_t  le_uint64;  // Little Endian 64 bit unsigned int
            char [64]  internal_string;
            char *     name;      // Example assumes LE 32 bit
        }
        example_struct = BPStruct(le_32='<I', be_int16='>h', le_uint64='<Q',
                                    internal_string='64s', name='<I')

        example_struct.read(qemu, addr)
        print(example_struct)

        Its recommended to subclass this class as follows and just define FORMAT in the class
        as a dictionary
        ExampleStruct(BP_Struct):
            FORMAT = {
                le_32: "<I",
                be_16: ">h",
                internal_string: "64s",
                p_name: ">I"
            }
        es = ExampleStruct()
        es.internal_string = "Test Struct"
        es.build_buffer()
    """

    def __init__(self, **kwargs):
        if not kwargs and hasattr(self, "FORMAT"):
            kwargs = self.FORMAT

        self._fields_desc = kwargs
        overlapping_keys = (self.__dict__.keys()) & set(kwargs.keys())
        if overlapping_keys:
            raise KeyError(f"Invalid field names {overlapping_keys}")

        # Validate that format strings are valid
        bad_fields = {}
        for field_name, fmt in self._fields_desc.items():
            try:
                struct.calcsize(fmt)
            except struct.error:
                bad_fields[field_name] = fmt
        if bad_fields:
            raise ValueError(
                f"Entry Values must be valid struct format strings."
                f"Bad Fields: {bad_fields}"
            )

        for key in kwargs:
            setattr(self, key, None)

    def read(self, qemu, pointer):
        """
        Reads the structure from QEMU using the pointer.
        """
        buffer = qemu.read_memory(pointer, 1, len(self), raw=True)
        self.parse_buffer(buffer)

    def parse_buffer(self, buffer):
        """
        Parses the buffer into the fields
        """
        offset = 0
        for field_name, fmt in self._fields_desc.items():
            size = struct.calcsize(fmt)
            value = struct.unpack(fmt, buffer[offset : offset + size])[0]
            setattr(self, field_name, value)
            offset += size

    def write(self, qemu, pointer, default=None):
        """
        Reads the structure from QEMU using the pointer.
        """
        qemu.write_memory(pointer, self.build_buffer(default))

    def build_buffer(self, default_value=None):
        """
        Builds a flat buffer suitable for writing to memory

        :param default_value:  byte to fill fields with
        """
        buffer = b""

        def_value = b"\00" if default_value is None else default_value
        for field_name, fmt in self._fields_desc.items():
            value = getattr(self, field_name)
            if value is None:
                buffer = def_value * struct.calcsize(fmt)
            else:
                buffer += struct.pack(fmt, value)
        return buffer

    def __len__(self):
        size = 0
        for _, fmt in self._fields_desc.items():
            size += struct.calcsize(fmt)
        return size

    def __repr__(self):
        out = [f"{self.__class__.__name__} {{"]
        for field_name in self._fields_desc:
            value = getattr(self, field_name)
            if isinstance(value, int):
                out.append(f"  {field_name}: {value} ({value:#x})")
            else:
                out.append(f"  {field_name}: {value}")
        out.append("}")
        return "\n".join(out)
