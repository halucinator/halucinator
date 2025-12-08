#!/usr/bin/env bash
set -e
set -x
#move into the folder where this script is reguardless of where it's run from
export SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd $SCRIPT_DIR
#start uart console
mkfifo ./in1
touch ./test_out.txt
hal_dev_uart --id=0 --newline <./in1 >./test_out.txt &
exec 3>./in1
#run halucinator
bash ./run.sh </dev/null >./hal_out.txt &
#check that halucinator gives expected output
until grep "HAL_LOG|INFO|  SkipFunc: z_clock_driver_init" ./hal_out.txt
do
    sleep 2
    tail -n 5 ./hal_out.txt
done
echo "halucinator running as expected"
sleep 5
#check that uart gets expected output
function check_output () {
   cd $SCRIPT_DIR
   until {
	   grep "$0" ./test_out.txt
         }
    do
      sleep 2
      tail -n 1 ./test_out.txt
    done
}
export -f check_output
#set a timeout for checking uart output
timeout 3m bash -c check_output "Enter a line"
echo  "This is the input" >&3
timeout 3m bash -c check_output "line: This is the input"
