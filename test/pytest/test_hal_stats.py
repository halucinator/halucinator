"""
Unit test for hal stats
"""

import os

import pytest
import yaml

from halucinator import hal_stats

TEST_FILE_NAME = "stat.yml"


def delete_file(file_name):
    if os.path.exists(file_name):
        os.remove(file_name)


def clear_stats():
    hal_stats.stats = {}


class TestHalStats:
    def test_stat_filename_set_correctly(self):
        FILE_NAME = "stats.txt"
        hal_stats.set_filename(FILE_NAME)
        assert hal_stats._stats_file == FILE_NAME

    def test_one_value_for_existing_key_added_correctly(self):
        clear_stats()
        hal_stats.stats["key1"] = set()
        delete_file(TEST_FILE_NAME)
        hal_stats.set_filename(TEST_FILE_NAME)
        hal_stats.write_on_update("key1", 101)
        with open(TEST_FILE_NAME, "r") as f:
            stat_data = yaml.load(f, Loader=yaml.FullLoader)
            assert stat_data["key1"] == {101}

    def test_two_values_for_existing_key_added_correctly(self):
        clear_stats()
        hal_stats.stats["key1"] = set()
        delete_file(TEST_FILE_NAME)
        hal_stats.set_filename(TEST_FILE_NAME)
        hal_stats.write_on_update("key1", 101)
        hal_stats.write_on_update("key1", 200)
        with open(TEST_FILE_NAME, "r") as f:
            stat_data = yaml.load(f, Loader=yaml.FullLoader)
            assert stat_data["key1"] == {101, 200}

    def test_values_for_two_existing_keys_added_correctly(self):
        clear_stats()
        hal_stats.stats["key1"] = set()
        hal_stats.stats["key2"] = set()
        delete_file(TEST_FILE_NAME)
        hal_stats.set_filename(TEST_FILE_NAME)
        hal_stats.write_on_update("key1", 101)
        hal_stats.write_on_update("key1", 200)
        hal_stats.write_on_update("key2", 300)
        with open(TEST_FILE_NAME, "r") as f:
            stat_data = yaml.load(f, Loader=yaml.FullLoader)
            assert stat_data["key1"] == {101, 200}
            assert stat_data["key2"] == {300}

    def test_adding_value_for_non_existing_causes_exception(self):
        clear_stats()
        hal_stats.stats["key1"] = set()
        hal_stats.stats["key2"] = set()
        delete_file(TEST_FILE_NAME)
        hal_stats.set_filename(TEST_FILE_NAME)
        with pytest.raises(KeyError, match="key3"):
            hal_stats.write_on_update("key3", 200)
