#!/bin/bash
# Copyright 2026 Christopher Wright

PYTHONUNBUFFERED=1 halucinator --emulator "${HAL_EMULATOR:-unicorn}" \
  -c test/multi_arch_irq/x86/test_irq_config.yaml \
  -c test/multi_arch_irq/x86/test_irq_addrs.yaml \
  -c test/multi_arch_irq/x86/test_irq_memory.yaml \
  -n x86_irq_test
