#!/usr/bin/env bash
set -e
set -x
#move into the folder where this script is reguardless of where it's run from
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd $SCRIPT_DIR
#start uart console
hal_dev_uart --id=0 --newline </dev/null >./test_out.txt &
#run halucinator
{ bash ./run.sh </dev/null >./hal_out.txt || true } &
#check that halucinator gives expected output
while ! grep "HAL_LOG|INFO|  SkipFunc: z_clock_driver_init" ./hal_out.txt; do
    sleep 2
    tail -n 5 ./hal_out.txt
done
sleep 5
#check that uart gets expected output
function check_output {
   SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
   cd $SCRIPT_DIR
   until grep "All tests complete." ./test_out.txt; do
      sleep 2
      tail -n 5 ./test_out.txt
    done
}
export -f check_output
#set a timeout for checking uart output
timeout 5m bash -c check_output
pkill -9 hal_dev_uart || true
pkill -9 halucinator || true
pkill -9 arm-none-eabi-gdb || true
