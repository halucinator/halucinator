#!/bin/bash
##############################################################################
#
# File: run_demo.sh
#
# Description: Demo libmatch using OpenDPS
#
# Usage: ./run_demo.sh
#
# Note: This script assumes libmatch is installed and it is running in the
# same directory as the OpenDPS demo files.
#
##############################################################################

rm -f opendps.json opendps.yaml libopencm3_stm32f1.lmdb

libmatch -l libopencm3_stm32f1.a --lmdb-dir libopencm3
libmatch -t opendps.elf --lmdb-dir libopencm3 -j opendps.json -y opendps.yaml

# Optional step - compare results to an "answer key"
# python evaluate_libmatch_performance.py -i opendps.json -r opendps.total_lib.map
