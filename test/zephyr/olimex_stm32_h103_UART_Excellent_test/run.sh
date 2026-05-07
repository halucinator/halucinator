#! /bin/bash


halucinator -c zephyr_memory.yaml -c olimex_h103_boot.yaml -c uart_breakpoints.yaml -c zephyr_addrs.yaml --log_blocks=trace-nochain

