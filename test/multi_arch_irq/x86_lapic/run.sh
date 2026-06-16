#!/bin/bash
# Copyright 2026 Christopher Wright

# QEMU/avatar path: real i386 QEMU + LAPIC interrupt injection.
HAL_QEMU_LOG=/tmp/x86_qemu_dbg.log PYTHONUNBUFFERED=1 halucinator --emulator "${HAL_EMULATOR:-qemu}" \
  -c test/multi_arch_irq/x86_lapic/test_irq_config.yaml \
  -c test/multi_arch_irq/x86_lapic/test_irq_addrs.yaml \
  -c test/multi_arch_irq/x86_lapic/test_irq_memory.yaml \
  -n x86_lapic_irq_test
