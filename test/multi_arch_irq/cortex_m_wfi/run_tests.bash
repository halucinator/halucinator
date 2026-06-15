#!/usr/bin/env bash
# Copyright 2026 Christopher Wright

# Reproduction harness for upstream issue #31 — "Exit WFI sleep state by
# IRQ injection". Mirrors test/multi_arch_irq/cortex_m/run_tests.bash but
# runs the WFI firmware variant: the CPU halts in WFI before the IRQ is
# injected. PASS = the injected IRQ wakes the core and "IRQ 17 FIRED"
# appears. If WFI is never exited, the test times out (bug reproduced).

set -x

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt
rm -rf tmp/cortex_m_wfi_irq_test

[ -n "${HALUCINATOR_QEMU_ARM:-}" ] && export HALUCINATOR_QEMU_ARM
PYTHONUNBUFFERED=1 \
    bash ./test/multi_arch_irq/cortex_m_wfi/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

TIMEOUT=60
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "UART TX:b'READY" hal_out.txt 2>/dev/null; then
        echo "Firmware reached READY (halted in WFI) after ${ELAPSED}s"
        break
    fi
    if ! kill -0 $HAL_PID 2>/dev/null; then
        echo "halucinator exited before READY:"; tail -40 hal_out.txt; exit 1
    fi
    sleep 1; ELAPSED=$((ELAPSED + 1))
done
if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "FAIL: timed out waiting for READY"; tail -40 hal_out.txt
    kill -9 $HAL_PID || true; exit 1
fi

# CPU is now halted in WFI. Inject IRQ #17 from an external device.
hal_dev_irq_trigger -i 17 || true
sleep 2

TIMEOUT=30
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "IRQ 17 FIRED" hal_out.txt 2>/dev/null; then SUCCESS=1; break; fi
    if grep -q "HARDFAULT" hal_out.txt 2>/dev/null; then
        echo "FAIL: firmware took a HardFault"; break; fi
    sleep 1; ELAPSED=$((ELAPSED + 1))
done

pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "Cortex-M WFI IRQ test PASSED (WFI woke on injected IRQ)"; exit 0
fi
echo "FAIL: WFI never exited — IRQ injection did not wake the core (issue #31 reproduced)"
echo "Last 40 lines of hal_out.txt:"; tail -40 hal_out.txt
exit 1
