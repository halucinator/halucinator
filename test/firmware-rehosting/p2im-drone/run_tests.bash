#!/usr/bin/env bash
set -e
set -x

# Clean up any leftover processes from previous tests
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2

# Move into the folder where this script is regardless of where it's run from
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

# Auto-detect QEMU if not set
if [ -z "$HALUCINATOR_QEMU_ARM" ]; then
    # Check common locations
    for candidate in \
        "$SCRIPT_DIR/../../../deps/build-qemu/arm-softmmu/qemu-system-arm" \
        "$SCRIPT_DIR/../../../deps/avatar-qemu/build/qemu-system-arm"; do
        if [ -x "$candidate" ]; then
            export HALUCINATOR_QEMU_ARM="$(realpath "$candidate")"
            echo "Auto-detected QEMU: $HALUCINATOR_QEMU_ARM"
            break
        fi
    done
    if [ -z "$HALUCINATOR_QEMU_ARM" ]; then
        echo "ERROR: HALUCINATOR_QEMU_ARM not set and QEMU not found in deps/"
        exit 1
    fi
fi

# Set up project module so project.bp_handlers.* imports resolve
mkdir -p /tmp/drone_pythonpath
ln -sfn "$SCRIPT_DIR" /tmp/drone_pythonpath/project

# Clean up any previous runs
rm -f ./hal_out.txt ./test_out.txt
touch ./hal_out.txt ./test_out.txt

# Start halucinator in background
bash ./run.sh </dev/null >./hal_out.txt 2>&1 &
HAL_PID=$!

# Wait for halucinator to be ready
echo "Waiting for halucinator to initialize..."
TIMEOUT=120
ELAPSED=0
while ! grep -q "Letting QEMU Run" ./hal_out.txt; do
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "TIMEOUT waiting for halucinator to initialize"
        cat ./hal_out.txt
        kill $HAL_PID 2>/dev/null || true
        exit 1
    fi
done
echo "Halucinator running, waiting for firmware init..."
sleep 3

# Send the handshake character via the drone.py IO server
echo "Sending handshake..."
PYTHONPATH=/tmp/drone_pythonpath python3 -c "
from halucinator.external_devices.ioserver import IOServer
import time
io = IOServer(5556, 5555)
io.start()
time.sleep(2)
io.send_msg('Peripheral.UARTPublisher.rx_data', {'id': 0x40013800, 'chars': 'g'})
time.sleep(5)
io.shutdown()
" 2>/dev/null

# Verify the drone control loop is running
echo "Checking for drone control loop output..."
if grep -q "MPU9250_Init\|accel x/y/z" ./hal_out.txt && grep -q "UART TX\|MSP_Send" ./hal_out.txt; then
    echo "P2IM Drone e2e test PASSED - control loop active"
else
    echo "P2IM Drone e2e test FAILED"
    echo "=== HAL OUTPUT (last 30 lines) ==="
    tail -30 ./hal_out.txt
    kill $HAL_PID 2>/dev/null || true
    exit 1
fi

# Clean up
kill $HAL_PID 2>/dev/null || true
pkill -f qemu-system-arm 2>/dev/null || true
sleep 1
echo "Done"
