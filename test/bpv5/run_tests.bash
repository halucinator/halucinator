#!/usr/bin/env bash
# Smoke test: boot Bus Pirate v5 firmware under HALucinator and verify that
# the firmware reaches the HiZ> command prompt.
#
# The test subscribes to the UTTYModel ZMQ topic that the BusPirateConsole
# handler publishes CDC write output to, and waits for the "HiZ>" string to
# appear in the firmware's TX stream.
#
# Expected pass time: ~30s (QEMU startup + GDB connect + firmware boot).
# Hard timeout: 120s.

set -e

pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator     2>/dev/null || true
sleep 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# src/ for halucinator package; . for test.bpv5.bpv5_handlers import
export PYTHONPATH=src:.

rm -f bpv5_smoke_out.txt

# Write the ZMQ subscriber to a temp file to avoid heredoc quoting issues.
TMPPY=$(mktemp /tmp/bpv5_monitor_XXXXXX.py)
trap 'rm -f "$TMPPY"' EXIT

cat > "$TMPPY" << 'PYEOF'
import sys
import time
from halucinator.external_devices.ioserver import IOServer

output = bytearray()
found = False

def on_tx(server, msg):
    global found
    raw = msg.get('chars', b'')
    if isinstance(raw, (bytes, bytearray)):
        output.extend(raw)
    elif isinstance(raw, list):
        output.extend(bytes(raw))
    if b'HiZ>' in output:
        found = True

# rx_port=5556: subscribe to halucinator's PUB socket (firmware TX output)
# tx_port=5555: connect to halucinator's SUB socket (firmware RX input)
io = IOServer(rx_port=5556, tx_port=5555)
io.register_topic('Peripheral.UTTYModel.tx_buf', on_tx)
io.start()

deadline = time.time() + 120
while time.time() < deadline and not found:
    time.sleep(0.25)

io.shutdown()

if found:
    print('SUCCESS: HiZ> prompt received', file=sys.stderr)
    sys.exit(0)
else:
    print('TIMEOUT: HiZ> not seen within 120s', file=sys.stderr)
    sys.exit(1)
PYEOF

# Start the subscriber before halucinator so the ZMQ connection is pending
# before the peripheral server binds — avoids any early-message race.
python3 "$TMPPY" &
MONITOR_PID=$!

echo "=== Starting halucinator for BPv5 smoke test ==="
PYTHONUNBUFFERED=1 halucinator \
    -c test/bpv5/bpv5_memory.yaml \
    -c test/bpv5/bpv5_config.yaml \
    -c test/bpv5/bpv5_addrs.yaml \
    -n bpv5_smoke \
    >bpv5_smoke_out.txt 2>&1 &
HAL_PID=$!

wait $MONITOR_PID
RC=$?

kill $HAL_PID 2>/dev/null || true
pkill -f qemu-system-arm 2>/dev/null || true

if [ $RC -eq 0 ]; then
    echo "BPv5 smoke test PASSED"
    exit 0
else
    echo "BPv5 smoke test FAILED"
    echo "=== bpv5_smoke_out.txt (last 50 lines) ==="
    tail -50 bpv5_smoke_out.txt || true
    exit 1
fi
