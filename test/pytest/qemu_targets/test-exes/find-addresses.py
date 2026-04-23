#!/usr/bin/env python3

"""Finds the addresses used in the test_arm_qemu tests.

Right now, this is specialied specifically for 'test.c', with
hard-coded function names and other attributes. If more tests are
added in the future, this will need to be generalied to support that.

It's also hard-coded for ARM.

If test.c is updated, run this to re-gather addresses, then paste into
test_arm_qemu.py. (If this happens a lot, maybe figure out a better
way to handle that, but with rare updates this should be fine.)

"""

import re
import subprocess
import sys


# Will have to do something smarter if we need this to run on non-ARM
# targets, but this will do for now.
#
# The 'pattern's below might change, and the 'call_pattern'
# *definitely* will change, so expect that if you *do* need to add
# support for other archs.
OBJDUMP = "arm-linux-gnueabi-objdump"


######
# This should be kept in sync with that in test_arm_qemu
pattern = """
ADDRS = Addresses(
    load=0x{load:X},
    main=0x{main:X},
    arguments_check=0x{arguments_check:X},
    breakpoint_check_arguments_check_return=0x{breakpoint_check_arguments_check_return:X},
    return_12=0x{return_12:X},
    breakpoint_check_twelve=0x{breakpoint_check_twelve:X},
    return_site_from_arguments_check=0x{return_site_from_arguments_check:X},
)
"""


def find_addresses_by_re(lines, pattern):
    compiled = re.compile(pattern)
    matches = []
    for line in lines:
        match = re.fullmatch(compiled, line)
        if match:
            address_text = match.group("address")
            address_num = int(address_text, 16)
            matches.append(address_num)
    return matches


def find_function_return_site(lines, name):
    pattern = b" +(?P<address>[0-9A-Fa-f]+):.*bl.* <" + name.encode() + b">"
    matches = find_addresses_by_re(lines, pattern)
    assert len(matches) == 1
    call_address = matches[0]
    return call_address + 4  # return site


def find_function_address(lines, name):
    pattern = b"(?P<address>[0-9A-Fa-f]+) <" + name.encode() + b">:"
    matches = find_addresses_by_re(lines, pattern)
    assert len(matches) == 1
    return matches[0]


def find_load_address(lines):
    pattern = b"(?P<address>[0-9A-Fa-f]+) <[A-Za-z0-9_]+>:"
    matches = find_addresses_by_re(lines, pattern)
    return matches[0]


def main():
    filename = sys.argv[1]
    disassembly = subprocess.check_output([OBJDUMP, "--disassemble", filename])
    lines = disassembly.split(b"\n")

    print(
        pattern.format(
            load=find_load_address(lines),
            main=find_function_address(lines, "main"),
            arguments_check=find_function_address(lines, "arguments_check"),
            breakpoint_check_arguments_check_return=find_function_address(
                lines, "breakpoint_check_arguments_check_return"
            ),
            return_12=find_function_address(lines, "return_12"),
            breakpoint_check_twelve=find_function_address(
                lines, "breakpoint_check_twelve"
            ),
            return_site_from_arguments_check=find_function_return_site(
                lines, "arguments_check"
            ),
        )
    )


if __name__ == "__main__":
    main()
