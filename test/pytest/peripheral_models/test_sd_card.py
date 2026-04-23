import os
from copy import copy
from unittest import mock

import pytest

from halucinator.peripheral_models.sd_card import SDCardModel


class XfailException(Exception):
    """
    Custom exception indicating xfailure.
    """

    pass


PERIPHERAL_SERVER_BASE_DIR_VALUES = ["peripheral_server_base_dir", None]
SD_CARD_CONFIG_QWARGS = (
    {"sd_id": 0, "filename": None, "block_size": 0x100},
    {"sd_id": 1, "filename": "sd_card_1_filename", "block_size": 0x200},
)
SD_CARD_IDS = [qwargs["sd_id"] for qwargs in SD_CARD_CONFIG_QWARGS]


def sd_card_config_qwargs(sd_id):
    return next(
        qwargs for qwargs in SD_CARD_CONFIG_QWARGS if qwargs["sd_id"] == sd_id
    )


@pytest.fixture(autouse=True)
def reset_config():
    SDCardModel.BLOCK_SIZE.clear()
    SDCardModel.filename.clear()
    yield


@pytest.mark.xfail(
    raises=XfailException, reason="cls.BLOCK_SIZE[sd_id] is hardcoded"
)
def test_set_config_hardcodes_block_size():
    sd_id, filename, block_size = (0, None, 0x100)
    SDCardModel.set_config(sd_id, filename, block_size)
    hardcoded_block_size = SDCardModel.BLOCK_SIZE[sd_id]
    SDCardModel.set_config(sd_id, filename, hardcoded_block_size + 1)
    try:
        assert SDCardModel.BLOCK_SIZE[sd_id] == hardcoded_block_size + 1
    except AssertionError as ex:
        raise XfailException from ex


@pytest.mark.xfail(
    raises=XfailException,
    reason="module 'halucinator.peripheral_models.peripheral_server' has no attribute 'base_dir'",
)
def test_set_config_yields_attribute_error():
    sd_id, filename, block_size = (0, "sd_card.bin", 0x100)
    try:
        SDCardModel.set_config(sd_id, filename, block_size)
    except AttributeError as ex:
        if (
            str(ex)
            == "module 'halucinator.peripheral_models.peripheral_server' has no attribute 'base_dir'"
        ):
            raise XfailException
        else:
            raise


@pytest.mark.parametrize("base_dir", PERIPHERAL_SERVER_BASE_DIR_VALUES)
def test_patched_set_config_updates_filenames_and_block_sizes(base_dir,):
    with mock.patch(
        "halucinator.peripheral_models.sd_card.peripheral_server",
        mock.Mock(base_dir=base_dir),
    ):
        for qwargs in SD_CARD_CONFIG_QWARGS:
            SDCardModel.set_config(**qwargs)
            SDCardModel.BLOCK_SIZE[qwargs["sd_id"]] = qwargs["block_size"]
    for sd_id in SD_CARD_IDS:
        filename_arg = sd_card_config_qwargs(sd_id)["filename"]
        assert (sd_id in SDCardModel.filename) == (filename_arg is not None)
        assert (sd_id not in SDCardModel.filename) or SDCardModel.filename[
            sd_id
        ] == (
            filename_arg
            if base_dir is None
            else os.path.join(base_dir, filename_arg)
        )
        assert (
            SDCardModel.BLOCK_SIZE[sd_id]
            == sd_card_config_qwargs(sd_id)["block_size"]
        )


@pytest.mark.xfail(
    raises=XfailException,
    reason="module 'halucinator.peripheral_models.peripheral_server' has no attribute 'base_dir'",
)
def test_get_filename_yields_attribute_error():
    try:
        SDCardModel.get_filename(0x0)
    except AttributeError as ex:
        if (
            str(ex)
            == "module 'halucinator.peripheral_models.peripheral_server' has no attribute 'base_dir'"
        ):
            raise XfailException
        else:
            raise


@pytest.mark.parametrize("base_dir", PERIPHERAL_SERVER_BASE_DIR_VALUES)
def test_patched_get_filename_for_new_cards_updates_configured_filenames(
    base_dir,
):
    filenames_set = set()
    for sd_id in SD_CARD_IDS:
        assert sd_id not in SDCardModel.filename
        with mock.patch(
            "halucinator.peripheral_models.sd_card.peripheral_server",
            mock.Mock(base_dir=base_dir),
        ):
            filename = SDCardModel.get_filename(sd_id)
        assert os.path.dirname(filename) == (
            "" if base_dir is None else base_dir
        )
        assert SDCardModel.filename[sd_id] == filename
        filenames_set.add(filename)
    assert len(filenames_set) == len(SD_CARD_IDS)


@pytest.fixture(params=["peripheral_server_base_dir", None])
def patched_sd_card_model_setup(reset_config, request):
    # Patch missing module attribute peripheral_server.base_dir.
    peripheral_server_base_dir = request.param
    with mock.patch(
        "halucinator.peripheral_models.sd_card.peripheral_server",
        mock.Mock(base_dir=peripheral_server_base_dir),
    ):
        if not (
            peripheral_server_base_dir is None
            or os.path.isdir(peripheral_server_base_dir)
        ):
            os.mkdir(peripheral_server_base_dir)
        for qwargs in SD_CARD_CONFIG_QWARGS:
            SDCardModel.set_config(**qwargs)
            # Patch hardcoded block_size value.
            SDCardModel.BLOCK_SIZE[qwargs["sd_id"]] = qwargs["block_size"]
        yield peripheral_server_base_dir
        for sd_id in SD_CARD_IDS:
            if os.path.exists(SDCardModel.get_filename(sd_id)):
                os.remove(SDCardModel.get_filename(sd_id))
        SDCardModel.BLOCK_SIZE.clear()
        SDCardModel.filename.clear()


def test_get_filename_for_existing_cards_yields_configured_filename(
    patched_sd_card_model_setup,
):
    filename_saved = copy(SDCardModel.filename)
    for sd_id in SD_CARD_IDS:
        filename = SDCardModel.get_filename(sd_id)
        # Yields configured filemane.
        assert filename == SDCardModel.filename[sd_id]
    # Does not modify configured fileneames.
    assert all(
        SDCardModel.filename[sd_id] == filename_saved[sd_id]
        for sd_id in filename_saved
    )


def test_read_block_after_write_block_yields_written_data(
    patched_sd_card_model_setup,
):
    block_nums = (0, 4, 2)

    def written_data(block_num, block_size):
        return str(block_num).encode() * block_size

    for sd_id in SD_CARD_IDS:
        for block_num in block_nums:
            rv = SDCardModel.write_block(
                sd_id,
                block_num,
                written_data(block_num, SDCardModel.BLOCK_SIZE[sd_id]),
            )
            assert rv is True
    for sd_id in SD_CARD_IDS:
        for block_num in block_nums:
            data = SDCardModel.read_block(sd_id, block_num)
            assert data == written_data(
                block_num, SDCardModel.BLOCK_SIZE[sd_id]
            )


def test_get_block_size_yields_configured_block_size(
    patched_sd_card_model_setup,
):
    for sd_id in SD_CARD_IDS:
        assert (
            SDCardModel.get_block_size(sd_id) == SDCardModel.BLOCK_SIZE[sd_id]
        )
