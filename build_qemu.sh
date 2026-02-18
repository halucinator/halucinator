#!/bin/bash
# . /etc/bash_completion
# usage build target-list [arm-softmmu, ppc-softmmu, aarch64-softmmu]
set -o errexit

if [ "$1" == "" ] ; then
    TARGET_LIST=("ppc-softmmu" "arm-softmmu" "aarch64-softmmu" "mips-softmmu" "ppc64-softmmu")
else
    TARGET_LIST=$@
fi

# build qemu
for target in ${TARGET_LIST[@]}
do
    pushd deps/
    mkdir -p build-qemu/"$target"
    cd build-qemu/"$target"
    ../../libafl-qemu-bridge/configure --target-list=$target
    make all -j`nproc`
    popd
done

