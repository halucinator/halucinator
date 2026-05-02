#! /bin/bash


halucinator -c zephyr_memory.yaml -c frdm_k64f_boot.yaml -c uart_breakpoints.yaml -c zephyr_addrs.yaml --log_blocks=trace-nochain

