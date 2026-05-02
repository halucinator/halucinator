#!/usr/bin/env bash

set -e
set -x
# Clean up any leftover processes from previous tests
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

rm -f ./hal_out.txt ./test_out.txt
rm -rf tmp/Uart_Example

# Pre-flight: verify QEMU binary works
echo "=== QEMU pre-flight check ==="
"${HALUCINATOR_QEMU_ARM}" --version || { echo "QEMU binary failed to run"; exit 1; }
gdb-multiarch --version | head -1
ldd "${HALUCINATOR_QEMU_ARM}" | grep "not found" && { echo "QEMU has missing shared libs"; exit 1; } || true
echo "=== Pre-flight OK ==="

# Launch halucinator with retry — avatar2's GDB connect has a short
# timeout (5s) and can fail if QEMU is slow to start on CI runners.
MAX_ATTEMPTS=3
for ATTEMPT in $(seq 1 $MAX_ATTEMPTS); do
    echo "=== Halucinator launch attempt $ATTEMPT/$MAX_ATTEMPTS ==="
    rm -f ./hal_out.txt
    rm -rf tmp/Uart_Example
    PYTHONUNBUFFERED=1 ./test/STM32/example/run.sh </dev/null >hal_out.txt 2>&1 &
    HAL_PID=$!

    # Wait for firmware UART prompt
    TIMEOUT=120
    ELAPSED=0
    STARTED=false
    while [ $ELAPSED -lt $TIMEOUT ]; do
        if grep -q "Enter 10 characters using keyboard :" ./hal_out.txt 2>/dev/null; then
            STARTED=true
            break
        fi
        if grep -q "GDBProtocol was unable to connect" ./hal_out.txt 2>/dev/null; then
            echo "GDB connect failed on attempt $ATTEMPT"
            echo "=== QEMU stderr ==="
            cat tmp/Uart_Example/Uart_Example_err.txt 2>/dev/null || echo "(no QEMU stderr)"
            echo "=== QEMU stdout ==="
            cat tmp/Uart_Example/Uart_Example_out.txt 2>/dev/null || echo "(no QEMU stdout)"
            echo "=== QEMU config ==="
            cat tmp/Uart_Example/Uart_Example_conf.json 2>/dev/null || echo "(no config)"
            break
        fi
        sleep 2
        ELAPSED=$((ELAPSED + 2))
    done

    if [ "$STARTED" = true ]; then
        break
    fi

    # Clean up failed attempt
    kill $HAL_PID 2>/dev/null || true
    pkill -9 -f qemu-system-arm 2>/dev/null || true
    pkill -9 -f gdb-multiarch 2>/dev/null || true
    sleep 3

    if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
        echo "FAILED after $MAX_ATTEMPTS attempts"
        echo "=== hal_out.txt ==="
        cat ./hal_out.txt
        exit 1
    fi
done

# Use Python to send input via zmq and read output — avoids fifo race conditions
python3 -c "
from halucinator.external_devices.ioserver import IOServer
from halucinator.external_devices.uart import UARTPrintServer
import time, sys

io = IOServer(5556, 5555)
uart = UARTPrintServer(io)
io.start()

# Wait for zmq subscription to propagate
time.sleep(5)

# Send '1234567890' as the UART input — retry a few times in case
# the subscription hasn't fully propagated
for attempt in range(3):
    uart.send_data(1073811456, '1234567890')
    print(f'Sent input via zmq (attempt {attempt+1})', file=sys.stderr)
    time.sleep(5)

# Wait for response and collect output
time.sleep(10)
io.shutdown()
" 2>&1 &
SENDER_PID=$!

# Wait for expected output in hal_out.txt
function check_output {
    until grep -q "Example Finished" ./hal_out.txt 2>/dev/null; do
        sleep 1
    done
}

export -f check_output
if ! timeout 5m bash -c check_output; then
    echo "TIMEOUT waiting for 'Example Finished'"
    echo "=== hal_out.txt (last 50 lines) ==="
    tail -50 ./hal_out.txt || true
    kill $SENDER_PID 2>/dev/null || true
    kill $HAL_PID 2>/dev/null || true
    pkill -f qemu-system-arm 2>/dev/null || true
    exit 1
fi

# Clean up
kill $SENDER_PID 2>/dev/null || true
kill $HAL_PID 2>/dev/null || true
pkill -f qemu-system-arm 2>/dev/null || true
echo "STM32 UART e2e test PASSED"
