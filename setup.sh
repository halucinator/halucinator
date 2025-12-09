#!/bin/bash
# . /etc/bash_completion

if [ "$1" == "" ] ; then
    TARGET_LIST=("ppc-softmmu" "arm-softmmu" "aarch64-softmmu" "mips-softmmu" "ppc64-softmmu")
else
    TARGET_LIST=$@
fi

pip install -e deps/avatar2/
pip install -r src/requirements.txt
pip install -e src


# build qemu
for target in ${TARGET_LIST[@]}
do
    ./build_qemu.sh $target
done
