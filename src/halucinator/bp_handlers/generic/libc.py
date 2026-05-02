"""Libc function break points"""
import logging
import re
from halucinator.bp_handlers.bp_handler import BPHandler, bp_handler

log = logging.getLogger(__name__)


class Libc6(BPHandler):
    """This class holds generic libc functionality, such as printf and puts"""

    @bp_handler(["puts"])
    def puts(self, qemu, addr):  # pylint: disable=no-self-use,unused-argument
        """int puts(const char *str)"""
        log.debug("puts 0x%08x", addr)
        print_string = qemu.read_string(qemu.get_arg(0))
        print(print_string)
        return True, 1

    @bp_handler(["printf"])
    def printf(self, qemu, bp_addr):  # pylint: disable=no-self-use,unused-argument
        """int printf(const char *format, ...)
        handles most formats, but we don't do anything special
        for length or for the n. The n should never have been created"""
        # pylint: disable=too-many-branches
        fmt = qemu.read_string(qemu.get_arg(0))

        formats = []  # We aren't handling anything fancy or even length args
        strsplit = re.split(r"(\W)", fmt)
        for i, element in enumerate(strsplit):
            if element == "%" and len(strsplit) > i + 1:
                if i >= 1 and strsplit[i - 1] != "\\":
                    formats.append(strsplit[i + 1])
        printf_args = []
        for i, form in enumerate(formats):
            arg_int = i + 1
            value = qemu.get_arg(arg_int)
            if "i" in form or "d" in form or "u" in form:  # int
                value = int(value)
            elif "f" in form or "F" in form:  # double in normal form
                value = float(value)
            elif "x" in form or "X" in form:  # hexidecimal
                value = int(value)
            elif "s" in form:  # null terminated string
                value = qemu.read_string(value)
            elif "c" in form:  # character
                value = qemu.read_string(value)[0]
            elif "e" in form or "E" in form:  # double in standard form
                value = float(value)
            elif "g" in form or "G" in form:  # double in normal or exponential form
                value = float(value)
            elif "o" in form:  # unsigned int in octal
                value = int(value)
            elif "a" in form or "A" in form:  # double in dex notation
                value = float(value)
            # elif "p" in form: #void pointer
            #     print("Void pointer")
            # # elif "n" in form:
            # # print nothing but writes the number of characters written so
            #   far into integer pointer parameter
            else:
                print("Unhandled format")
                print(f"format: {fmt}\n")
                return True, 1
            printf_args.append(value)

        print_string = fmt % tuple(printf_args)
        print(str(print_string), end="")
        log.info("%s", print_string)
        return True, len(print_string)

    @bp_handler(["exit"])
    def halucinator_exit(
        self, qemu, addr
    ):  # pylint: disable=no-self-use,unused-argument
        """
        Exits Halucinator when exit is called returning the
        status code exit was called with
        """
        ret_value = qemu.get_arg(0) & 0xFF
        qemu.halucinator_shutdown(ret_value)
        return False, None
