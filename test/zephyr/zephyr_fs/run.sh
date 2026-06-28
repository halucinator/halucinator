#! /bin/bash


halucinator --emulator "${HAL_EMULATOR:-avatar2}" -c zephyr_memory.yaml -c zephyr_config.yaml -c zephyr_addrs.yaml --log_blocks=trace-nochain

