#!/usr/bin/env bash

set -e
set -x
#test basic UART IO
#attach uart STIN to fifo
rm ./in1 || true
mkfifo ./in1
hal_dev_uart -i=1073811456 <./in1 >test_out.txt &
#run  halucinator
./test/STM32/example/run.sh </dev/null >hal_out.txt &
#wait for halucinator prompt
while ! grep "Enter 10 characters using keyboard :" ./hal_out.txt; do
    sleep 1
    tail -n 1 ./hal_out.txt
done
sleep 2
#open fifo and input string
exec 3>./in1
echo "1234567890\n" >&3
sleep 5
#check for expected output (with timeout if it fails)

function check_output {
    until {
      grep -q "1234567890" ./hal_out.txt && 
      grep -q "Example Finished" ./hal_out.txt
    }; do 
       sleep 1
       tail -n 1 ./hal_out.txt
       done
}

export -f check_output
timeout 1m bash -c check_output
#close fifo
exec 3>&-
rm ./in1
