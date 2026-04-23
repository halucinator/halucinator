from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.atmel_asf_v3.sd_mmc import SDCard

LR_RESET_VALUE = 0xFFFFFFFF

BLOCK_SIZE = 512
SOURCE_START_ADDRESS = 0x2400
SECOND_BLOCK_ADDRESS = 0x2600
assert SECOND_BLOCK_ADDRESS == SOURCE_START_ADDRESS + BLOCK_SIZE
BLOCK1_DATA = b"\0x25" * BLOCK_SIZE
BLOCK2_DATA = b"\0x78" * BLOCK_SIZE

MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    SOURCE_START_ADDRESS: [BLOCK1_DATA, 1, BLOCK_SIZE, True],
    SECOND_BLOCK_ADDRESS: [BLOCK2_DATA, 1, BLOCK_SIZE, True],
}


@pytest.fixture
def sd_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    qemu_mock.regs.lr = LR_RESET_VALUE
    return qemu_mock


@pytest.fixture
def sd():
    mock_model = mock.Mock()
    return SDCard(mock_model)


class TestSDCard:
    def test_log_only_just_returns_None(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # void
        # sd_mmc_init (
        #   void
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga72983cd374c0d84133fab99b0e724d9f
        continue_, ret_val = sd.log_only(sd_qemu_mock, None)
        assert not continue_
        assert ret_val == None

    def test_check_just_returns_zero(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_check (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga4a7a67cb312b43caa3ed99b920b8c081
        continue_, ret_val = sd.check(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_get_sd_type_just_returns_sd_type(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # card_type_t
        # sd_mmc_get_type (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga30d124ef12f70465d9dbbc5f879303a1
        CARD_TYPE_SD = 1
        continue_, ret_val = sd.get_sd_type(sd_qemu_mock, None)
        assert continue_
        assert ret_val == CARD_TYPE_SD

    def test_get_sd_version_returns_default_sd_version(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # card_version_t
        # sd_mmc_get_version (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga5eedda8d99dd390bf16a14f870e16105
        SD_VERSION = 0x20
        continue_, ret_val = sd.get_sd_version(sd_qemu_mock, None)
        assert continue_
        assert ret_val == SD_VERSION

    def test_get_capacity_returns_default_capacity(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # uint32_t
        # sd_mmc_get_capacity (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gab1594f7cfb859797e8b4a795518dcce5
        CAPACITY = 524288
        set_arguments(sd_qemu_mock, [0])
        continue_, ret_val = sd.get_capacity(sd_qemu_mock, None)
        assert continue_
        assert ret_val == CAPACITY

    def test_is_write_protected_returns_default_write_protection_status(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # bool
        # sd_mmc_is_write_protected (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga0740e31de47d3db2d21f6bd23f216497
        set_arguments(sd_qemu_mock, [0])
        continue_, ret_val = sd.is_write_protected(sd_qemu_mock, None)
        assert continue_
        assert not ret_val

    def test_is_write_protected_returns_write_protection_status_correctly(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # bool
        # sd_mmc_is_write_protected (
        #   uint8_t slot
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga0740e31de47d3db2d21f6bd23f216497
        sd.slot_configs = {
            1: {
                "capacity": 512 * 1024,
                "block_size": 512,
                "write_protected": True,
                "filename": "sd_image.img",
            }
        }
        set_arguments(sd_qemu_mock, [1])
        continue_, ret_val = sd.is_write_protected(sd_qemu_mock, None)
        assert continue_
        assert ret_val

    @pytest.mark.parametrize(
        "slot,expected_slot", [(0x01, 0x01), (0xFF, 0xFF), (0x2002, 0x02)]
    )
    @pytest.mark.parametrize("block", [0x02, 0x3004, 0x602010])
    @pytest.mark.parametrize(
        "nb,expected_nb",
        [(0x05, 0x05), (0xDEAD, 0xDEAD), (0xBEEFCCCC, 0xCCCC)],
    )
    def test_init_read_sets_read_slot_read_block_number_of_blocks_correctly(
        self, sd, sd_qemu_mock, slot, expected_slot, block, nb, expected_nb
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_init_read_blocks (
        #   uint8_t slot,
        #   uint32_t start,
        #   uint16_t nb_block
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gaaf241cff80ad9a222f1b59d20511c655
        set_arguments(sd_qemu_mock, [slot, block, nb])
        continue_, ret_val = sd.init_read(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert sd.active_read_slot == expected_slot
        assert sd.active_read_block == block
        assert sd.nb_blocks == expected_nb

    @pytest.mark.xfail
    def test_read_blocks_reads_two_blocks_and_writes_them_to_qemu_memory(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_start_read_blocks (
        #   void * dest,
        #   uint16_t nb_block
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#ga04bd58da58f6fb1a99c0763a520be86b
        DEST_ADDRESS = 0x1200
        NUMBER_OF_BLOCKS = 2
        FIRST_BLOCK = b"\0x25\0x30\0x44"
        SECOND_BLOCK = b"\0xFF\0xBB\0xDE\0xED"
        DATA = b"\0x25\0x30\0x44\0xFF\0xBB\0xDE\0xED"
        assert DATA == FIRST_BLOCK + SECOND_BLOCK
        READ_SLOT = 1
        READ_BLOCK = 101
        sd.active_read_slot = READ_SLOT
        sd.active_read_block = READ_BLOCK
        set_arguments(sd_qemu_mock, [DEST_ADDRESS, NUMBER_OF_BLOCKS])
        sd.model.read_block = mock.Mock(
            side_effect=[FIRST_BLOCK, SECOND_BLOCK]
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = sd.read_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        sd.model.read_block.assert_any_call(READ_SLOT, READ_BLOCK)
        sd.model.read_block.assert_any_call(READ_SLOT, READ_BLOCK + 1)
        sd_qemu_mock.write_memory.assert_called_once_with(
            DEST_ADDRESS, 1, DATA, len(DATA), raw=True
        )

    def test_end_read_blocks_clears_read_slot_and_read_block(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_wait_end_of_read_blocks (
        #   bool abort
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gae84a5054b70a4b259fd1f555dfae16ea
        BLOCK = 5
        SLOT = 4141
        sd.active_read_slot = BLOCK
        sd.active_read_block = SLOT
        continue_, ret_val = sd.end_read_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert sd.active_read_slot == None
        assert sd.active_read_block == None

    @pytest.mark.parametrize(
        "slot,expected_slot", [(0x02, 0x02), (0xEE, 0xEE), (0x4404, 0x04)]
    )
    @pytest.mark.parametrize("block", [0x05, 0x6008, 0x806040])
    @pytest.mark.parametrize(
        "nb,expected_nb",
        [(0x06, 0x06), (0xABCD, 0xABCD), (0xFEEDAAAA, 0xFEEDAAAA)],
    )
    def test_init_write_sets_write_slot_write_block_number_of_blocks_correctly(
        self, sd, sd_qemu_mock, slot, expected_slot, block, nb, expected_nb
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_init_write_blocks (
        #   uint8_t slot,
        #   uint32_t start,
        #   uint16_t nb_block
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gafb3cdb8be151fcc92b271c43c0403255
        set_arguments(sd_qemu_mock, [slot, block, nb])
        continue_, ret_val = sd.init_write(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert sd.active_write_slot == expected_slot
        assert sd.active_write_block == block
        assert sd.nb_blocks == expected_nb

    @pytest.mark.xfail
    def test_write_blocks_reads_two_blocks_from_qemu_memory_and_writes_them(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_start_write_blocks (
        #   const void * src,
        #   uint16_t nb_block
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gabcb031fde17177c2ae168985916ca564
        WRITE_SLOT = 0
        WRITE_BLOCK = 101
        NUMBER_OF_BLOCKS = 2
        sd.active_write_slot = WRITE_SLOT
        sd.active_write_block = WRITE_BLOCK
        set_arguments(sd_qemu_mock, [SOURCE_START_ADDRESS, NUMBER_OF_BLOCKS])
        sd.model.write_block = mock.Mock()
        continue_, ret_val = sd.write_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        sd.model.write_block.assert_any_call(
            WRITE_SLOT, WRITE_BLOCK, BLOCK1_DATA
        )
        sd.model.write_block.assert_any_call(
            WRITE_SLOT, WRITE_BLOCK + 1, BLOCK2_DATA
        )

    def test_end_write_blocks_clears_write_slot_and_write_block(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # sd_mmc_err_t
        # sd_mmc_wait_end_of_write_blocks (
        #   bool abort
        # )
        # The under test function's description can be found here -
        # https://asf.microchip.com/docs/latest/common2.components.memory.sd_mmc.example1.samd21_xplained_pro/html/group__sd__mmc__stack__group.html#gab84cf465732b3f0b8fa1e815beb28ccb
        BLOCK = 5
        SLOT = 4141
        sd.active_write_slot = BLOCK
        sd.active_write_block = SLOT
        continue_, ret_val = sd.end_write_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        assert sd.active_write_slot == None
        assert sd.active_write_block == None
