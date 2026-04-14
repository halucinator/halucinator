from unittest import mock

import pytest
from arm_helpers import create_read_memory_fake, set_arguments

from halucinator.bp_handlers.stm32f4.stm32f4_sd import SD_Card
from halucinator.peripheral_models.sd_card import SDCardModel

HW_INSTANCE_ADDRESS = 0x1000
HW_INSTANCE_ID = 0xDEAD

NUMBER_OF_BLOCKS = 2
ONE_BLOCK = 1
BLOCK1 = b"0123456789ABCDEF"
BLOCK2 = b"qazxswedcvfrtgbn"
BLOCK_SIZE = len(BLOCK1)
assert len(BLOCK1) == len(BLOCK2)
DATA_ADDRESS1 = 0x5500
DATA_ADDRESS2 = 0x5510
assert DATA_ADDRESS2 == DATA_ADDRESS1 + BLOCK_SIZE
BLOCK_NUMBER1 = 0x500
BLOCK_NUMBER2 = 0x501
assert BLOCK_NUMBER2 == BLOCK_NUMBER1 + 1


MEMORY = {
    # Parameters and return value structure for qemu memory_read function
    # <address>: [<return value>, <expected wordsize>, <expected number of words>, <raw - True or False>]
    # Missed values will be replaced with default values
    HW_INSTANCE_ADDRESS: [HW_INSTANCE_ID],
    DATA_ADDRESS1: [BLOCK1, 1, BLOCK_SIZE, True],
    DATA_ADDRESS2: [BLOCK2, 1, BLOCK_SIZE, True],
}


@pytest.fixture
def sd_qemu_mock(qemu_mock):
    qemu_mock.read_memory.side_effect = create_read_memory_fake(MEMORY)
    return qemu_mock


@pytest.fixture
def sd():
    return SD_Card()


class TestSD_Card:
    @mock.patch.object(SDCardModel, "set_config")
    def test_return_hal_ok_sets_config_correctly(
        self, set_config, sd, sd_qemu_mock
    ):
        SD_CARD_BLOCK_SIZE = 0x200
        # Associated HAL fuctions declaration
        # HAL_StatusTypeDef
        # HAL_SD_Init (
        #   SD_HandleTypeDef * hsd
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group1.html#gae810ba97b6cdbcb565e09a2bff888540
        # HAL_StatusTypeDef
        # HAL_SD_InitCard (
        #   SD_HandleTypeDef * hsd
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group1.html#ga2e71b088bca019b0a7e44016ca1e1707
        # HAL_StatusTypeDef
        # HAL_SD_DeInit (
        #   SD_HandleTypeDef * hsd
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group1.html#ga6bba9fce639c7d476dbd443b8c9e3117
        set_arguments(sd_qemu_mock, [HW_INSTANCE_ADDRESS])
        continue_, ret_val = sd.return_hal_ok(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        SDCardModel.set_config.assert_called_with(
            HW_INSTANCE_ID, None, SD_CARD_BLOCK_SIZE
        )

    @mock.patch.object(SDCardModel, "get_block_size", return_value=BLOCK_SIZE)
    @mock.patch.object(SDCardModel, "read_block", return_value=BLOCK1)
    def test_read_blocks_reads_one_block_and_write_it_to_qemu_memory(
        self, get_block_size, read_block, sd, sd_qemu_mock
    ):
        # Associated HAL fuctions declaration
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks,
        #   uint32_t Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#ga57c9c8243514fc0e9834093b76f299db
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks_IT (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaabd954880a6d21a0bf9a3c874e0f6a4d
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks_DMA	(
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#ga28918afbbec3039c3cef5ca52c923eb9

        set_arguments(
            sd_qemu_mock,
            [HW_INSTANCE_ADDRESS, DATA_ADDRESS1, BLOCK_NUMBER1, ONE_BLOCK],
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = sd.read_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        SDCardModel.read_block.assert_called_with(
            HW_INSTANCE_ID, BLOCK_NUMBER1
        )
        sd_qemu_mock.write_memory.assert_called_with(
            DATA_ADDRESS1, 1, BLOCK1, BLOCK_SIZE, raw=True
        )

    @mock.patch.object(SDCardModel, "get_block_size", return_value=BLOCK_SIZE)
    @mock.patch.object(SDCardModel, "read_block", side_effect=[BLOCK1, BLOCK2])
    def test_read_blocks_reads_two_block_and_write_it_to_qemu_memory(
        self, get_block_size, read_block, sd, sd_qemu_mock
    ):
        # Associated HAL fuctions declaration
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks,
        #   uint32_t Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#ga57c9c8243514fc0e9834093b76f299db
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks_IT (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaabd954880a6d21a0bf9a3c874e0f6a4d
        # HAL_StatusTypeDef
        # HAL_SD_ReadBlocks_DMA	(
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#ga28918afbbec3039c3cef5ca52c923eb9
        set_arguments(
            sd_qemu_mock,
            [
                HW_INSTANCE_ADDRESS,
                DATA_ADDRESS1,
                BLOCK_NUMBER1,
                NUMBER_OF_BLOCKS,
            ],
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = sd.read_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        SDCardModel.read_block.assert_any_call(HW_INSTANCE_ID, BLOCK_NUMBER1)
        SDCardModel.read_block.assert_any_call(HW_INSTANCE_ID, BLOCK_NUMBER2)
        sd_qemu_mock.write_memory.assert_any_call(
            DATA_ADDRESS1, 1, BLOCK1, BLOCK_SIZE, raw=True
        )
        sd_qemu_mock.write_memory.assert_any_call(
            DATA_ADDRESS2, 1, BLOCK2, BLOCK_SIZE, raw=True
        )

    @mock.patch.object(SD_Card, "blocks", {})
    @mock.patch.object(SDCardModel, "get_block_size", return_value=BLOCK_SIZE)
    @mock.patch.object(SDCardModel, "write_block")
    def test_write_blocks_reads_two_blocks_from_qemu_memory_and_write_it_to_sd_card(
        self, get_block_size, write_block, sd, sd_qemu_mock
    ):
        # Associated HAL fuctions declaration
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks,
        #   uint32_t Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gab2da788be2f14d72f9cfd4b9a647cf8c
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks_IT (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaeb46f1a16f34cec88b7bd5f429e6c48e
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks_DMA	(
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaf36737ac0370bcd8872f7c367e675b42
        set_arguments(
            sd_qemu_mock,
            [
                HW_INSTANCE_ADDRESS,
                DATA_ADDRESS1,
                BLOCK_NUMBER1,
                NUMBER_OF_BLOCKS,
            ],
        )
        continue_, ret_val = sd.write_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        SDCardModel.write_block.assert_any_call(
            HW_INSTANCE_ID, BLOCK_NUMBER1, BLOCK1
        )
        SDCardModel.write_block.assert_any_call(
            HW_INSTANCE_ID, BLOCK_NUMBER2, BLOCK2
        )
        assert SD_Card.blocks == {BLOCK_NUMBER1: BLOCK1, BLOCK_NUMBER2: BLOCK2}

    @mock.patch.object(SD_Card, "blocks", {})
    @mock.patch.object(SDCardModel, "get_block_size", return_value=BLOCK_SIZE)
    @mock.patch.object(SDCardModel, "write_block")
    def test_write_blocks_reads_one_block_from_qemu_memory_and_write_it_to_sd_card(
        self, get_block_size, write_block, sd, sd_qemu_mock
    ):
        # Associated HAL fuctions declaration
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks,
        #   uint32_t Timeout
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gab2da788be2f14d72f9cfd4b9a647cf8c
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks_IT (
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaeb46f1a16f34cec88b7bd5f429e6c48e
        # HAL_StatusTypeDef
        # HAL_SD_WriteBlocks_DMA	(
        #   SD_HandleTypeDef * hsd,
        #   uint8_t * pData,
        #   uint32_t BlockAdd,
        #   uint32_t NumberOfBlocks
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gaf36737ac0370bcd8872f7c367e675b42
        set_arguments(
            sd_qemu_mock,
            [HW_INSTANCE_ADDRESS, DATA_ADDRESS1, BLOCK_NUMBER1, ONE_BLOCK],
        )
        continue_, ret_val = sd.write_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        SDCardModel.write_block.assert_called_with(
            HW_INSTANCE_ID, BLOCK_NUMBER1, BLOCK1
        )
        assert SD_Card.blocks == {BLOCK_NUMBER1: BLOCK1}

    def test_erase_blocks_just_returns_zero(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_Erase (
        #   SD_HandleTypeDef * hsd,
        #   uint32_t BlockStartAdd,
        #   uint32_t BlockEndAdd
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32L486xx_User_Manual/group__sd__exported__functions__group2.html#gac2173726606e9e3a3ffce50f21fe0c38
        continue_, ret_val = sd.erase_blocks(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_get_card_CID_just_returns_zero(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_GetCardCID (
        #   SD_HandleTypeDef * hsd,
        #   HAL_SD_CardCIDTypeDef * pCID
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group4.html#ga8005b60470ddda128e571a97ce7b798f
        continue_, ret_val = sd.get_card_CID(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_get_card_CSD_writes_CSD_Struct_to_qemu_memory_correctly(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_GetCardCSD (
        #   SD_HandleTypeDef * hsd,
        #   HAL_SD_CardCSDTypeDef * pCSD
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group4.html#gae564ac3dff4eb7a4a325f4678e9fb183
        CSD_ADDRESS = 0x2600
        # This is the contents of an instance of __IO uint8_t HAL_SD_CardCSDTypeDef::CSDStruct
        # The description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/structhal__sd__cardcsdtypedef.html#a1d3831234ddd56400bb6f81bfa0ab16e
        CSD_STRUCT = bytes.fromhex(
            "0100000e0032b50509000000000001408a1d0000142c014020017f0000000209000000000100000000"
        )
        set_arguments(sd_qemu_mock, [HW_INSTANCE_ADDRESS, CSD_ADDRESS])
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = sd.get_card_CSD(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        sd_qemu_mock.write_memory.assert_called_with(
            CSD_ADDRESS, 1, CSD_STRUCT, len(CSD_STRUCT), raw=True
        )

    def test_get_card_status_just_returns_zero(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_GetCardStatus (
        #   SD_HandleTypeDef * hsd,
        #   HAL_SD_CardStatusTypeDef * pStatus
        # )
        # The under test function's description can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group4.html#ga87f73383fa9b477b920c96ff60645831
        continue_, ret_val = sd.get_card_status(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_get_card_info_writes_sd_card_info_to_QEMU_memory_correctly(
        self, sd, sd_qemu_mock
    ):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_GetCardInfo (
        #   SD_HandleTypeDef * hsd,
        #   HAL_SD_CardInfoTypeDef * pCardInfo
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group3.html#gab783bcbd433b03dd394308ec98e0fb88
        SD_CARD_INFO_ADDRESS = 0x3A00
        # The defintion of the SD Card Information Structure can be found here -
        # https://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/structhal__sd__cardinfotypedef.html
        SD_CARD_INFO = bytes.fromhex(
            "01000000"  # Card type
            "01000000"  # Card version
            "B5050000"  # Card class
            "AAAA0000"  # Relative card address (RelCardAddr)
            "002C7600"  # Capacity in blocks (BlockNbr)
            "00020000"  # Size of a SD card block (BlockSize)
            "002C7600"  # Capacity in logical blocks (LogBlockNbr)
            "00020000"  # Size of a logical block (LogBlockSize)
        )
        set_arguments(
            sd_qemu_mock, [HW_INSTANCE_ADDRESS, SD_CARD_INFO_ADDRESS]
        )
        sd_qemu_mock.write_memory = mock.Mock()
        continue_, ret_val = sd.get_card_info(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0
        sd_qemu_mock.write_memory.assert_called_with(
            SD_CARD_INFO_ADDRESS, 1, SD_CARD_INFO, len(SD_CARD_INFO), raw=True
        )

    def test_config_wide_bus_just_returns_zero(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # HAL_StatusTypeDef
        # HAL_SD_ConfigWideBusOperation (
        #   SD_HandleTypeDef * hsd,
        #   uint32_t WideMode
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group3.html#ga8395c55abfa691af95c004d1cb098323
        continue_, ret_val = sd.config_wide_bus(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 0

    def test_get_card_state_just_returns_four(self, sd, sd_qemu_mock):
        # Associated HAL fuction declaration
        # HAL_SD_CardStateTypeDef
        # HAL_SD_GetCardState (
        #   SD_HandleTypeDef * hsd
        # )
        # The under test function's description can be found here -
        # http://www.disca.upv.es/aperles/arm_cortex_m3/llibre/st/STM32F439xx_User_Manual/group__sd__exported__functions__group3.html#ga61f65802034a18141e92ae669fdc844c
        continue_, ret_val = sd.get_card_state(sd_qemu_mock, None)
        assert continue_
        assert ret_val == 4
