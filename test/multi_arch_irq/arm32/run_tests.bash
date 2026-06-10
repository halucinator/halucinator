#!/usr/bin/env bash
# End-to-end IRQ delivery test for the ARMv7-A firmware.
#
# Boots halucinator with the test_irq firmware, waits for READY in
# the UART output, then triggers IRQ #33 (SPI 1) via
# hal_dev_irq_trigger. The firmware's IRQ_Handler ACKs at GICC_IAR,
# sets a flag, and main() prints "IRQ 33 FIRED". Test passes if both
# READY and "IRQ 33 FIRED" appear.

set -e
set -x

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt
rm -rf tmp/arm32_irq_test

[ -n "${HALUCINATOR_QEMU_ARM:-}" ] && export HALUCINATOR_QEMU_ARM
PYTHONUNBUFFERED=1 bash ./test/multi_arch_irq/arm32/run.sh </dev/null \
    > hal_out.txt 2>&1 &
HAL_PID=$!

TIMEOUT=300
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "UART TX:b'READY" hal_out.txt 2>/dev/null; then
        echo "Firmware reached READY after ${ELAPSED}s"
        break
    fi
    if ! kill -0 $HAL_PID 2>/dev/null; then
        echo "halucinator exited before READY:"
        tail -40 hal_out.txt
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "FAIL: timed out waiting for READY"
    tail -40 hal_out.txt
    kill -9 $HAL_PID || true
    exit 1
fi

# Inject SPI 1 (IRQ ID 33). The firmware enabled IRQ 33 in
# GICD_ISENABLER1 and set its target/priority before entering its
# polling loop, so the asserted line is delivered to the CPU.
hal_dev_irq_trigger -i 33 || true
sleep 2

TIMEOUT=120
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if grep -q "IRQ 33 FIRED" hal_out.txt 2>/dev/null; then
        SUCCESS=1
        break
    fi
    if grep -q "ARM_FAULT" hal_out.txt 2>/dev/null; then
        echo "FAIL: firmware took an unexpected exception"
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

pkill -9 -f hal_dev_irq_trigger 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true

if [ "$SUCCESS" = "1" ]; then
    echo "ARM32 IRQ test PASSED"
    exit 0
fi

echo "FAIL: did not see IRQ 33 FIRED within ${TIMEOUT}s of injection"
echo "Last 30 lines of hal_out.txt:"
tail -30 hal_out.txt
exit 1
