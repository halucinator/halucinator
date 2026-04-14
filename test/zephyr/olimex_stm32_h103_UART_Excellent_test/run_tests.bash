#!/usr/bin/env bash
set -e
set -x
# Clean up any leftover processes from previous tests
pkill -9 -f qemu-system-arm 2>/dev/null || true
pkill -9 -f halucinator 2>/dev/null || true
pkill -9 -f hal_dev_uart 2>/dev/null || true
pkill -9 -f gdb-multiarch 2>/dev/null || true
sleep 2
#move into the folder where this script is regardless of where it's run from
export SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd $SCRIPT_DIR
rm -f ./in1 ./hal_out.txt ./test_out.txt
#start uart console
mkfifo ./in1
touch ./test_out.txt
hal_dev_uart --id=0 --newline <./in1 >./test_out.txt &
exec 3>./in1
#run halucinator
PYTHONUNBUFFERED=1 bash ./run.sh </dev/null >./hal_out.txt 2>&1 &
HAL_PID=$!
#check that halucinator gives expected output (with timeout)
TIMEOUT=120
ELAPSED=0
until grep -q "HAL_LOG|INFO|  SkipFunc: z_clock_driver_init" ./hal_out.txt 2>/dev/null; do
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "TIMEOUT waiting for halucinator"
        cat ./hal_out.txt
        kill $HAL_PID 2>/dev/null || true
        exit 1
    fi
done
echo "halucinator running as expected"
sleep 5
#check that uart gets expected output
function check_output () {
   cd $SCRIPT_DIR
   until grep -q "$1" ./test_out.txt 2>/dev/null; do
      sleep 2
    done
}
export -f check_output
#set a timeout for checking uart output
timeout 5m bash -c 'check_output "Enter a line"'
echo  "This is the input" >&3
timeout 5m bash -c 'check_output "line: This is the input"'
# clean up
exec 3>&-
rm -f ./in1
kill $HAL_PID 2>/dev/null || true
pkill -f qemu-system-arm 2>/dev/null || true
