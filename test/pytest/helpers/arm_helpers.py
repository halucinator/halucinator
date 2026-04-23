from unittest import mock

WORDSIZE = 4
ONE_WORD = 1


def set_arguments(qemu_mock, arguments):
    if len(arguments) > 0:
        qemu_mock.regs.r0 = arguments[0]
    if len(arguments) > 1:
        qemu_mock.regs.r1 = arguments[1]
    if len(arguments) > 2:
        qemu_mock.regs.r2 = arguments[2]
    if len(arguments) > 3:
        qemu_mock.regs.r3 = arguments[3]
    if len(arguments) > 4:
        # Must start putting arguments onto the stack here, but that
        # doesn't seem to be needed yet.
        assert False


def create_read_memory_fake(memory):
    def read_memory_fake(
        address, wordsize=WORDSIZE, num_words=ONE_WORD, raw=False
    ):
        params = memory.get(address, None)
        if params is not None:
            return_value = None
            expected_wordsize = WORDSIZE
            expected_num_word = ONE_WORD
            expected_raw = False
            if len(params) > 0:
                return_value = params[0]
            if len(params) > 1:
                expected_wordsize = params[1]
            if len(params) > 2:
                expected_num_word = params[2]
            if len(params) > 3:
                expected_raw = params[3]
            if len(params) > 4:
                assert False
            assert wordsize == expected_wordsize
            assert num_words == expected_num_word
            assert raw == expected_raw
            return return_value
        else:
            return mock.DEFAULT

    return read_memory_fake
