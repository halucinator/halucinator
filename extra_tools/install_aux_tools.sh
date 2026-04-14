#!/bin/bash
#
# Install multiple GCC ARM cross-compiler versions (4.8 through 10.2)
# Run inside a Docker container as root.
#
set -e

echo "Installing dependencies for ARM cross compilers"
dpkg --add-architecture i386
apt-get update && apt-get install -y libc6:i386 libncurses5:i386 libstdc++6:i386 bzip2 unzip

# Define compilers: NAME  GCC_VERSION  PRIORITY  URL
COMPILERS=(
    "gcc-arm-none-eabi-4_8-2013q4        4.8.3  10 https://launchpad.net/gcc-arm-embedded/4.8/4.8-2013-q4-major/+download/gcc-arm-none-eabi-4_8-2013q4-20131204-linux.tar.bz2"
    "gcc-arm-none-eabi-4_9-2014q4        4.9.3  20 https://launchpad.net/gcc-arm-embedded/4.9/4.9-2014-q4-major/+download/gcc-arm-none-eabi-4_9-2014q4-20141203-linux.tar.bz2"
    "gcc-arm-none-eabi-5_2-2015q4        5.2.1  30 https://launchpad.net/gcc-arm-embedded/5.0/5-2015-q4-major/+download/gcc-arm-none-eabi-5_2-2015q4-20151219-linux.tar.bz2"
    "gcc-arm-none-eabi-6_2-2016q4        6.2.1  40 https://developer.arm.com/-/media/Files/downloads/gnu-rm/6-2016q4/gcc-arm-none-eabi-6_2-2016q4-20161216-linux.tar.bz2"
    "gcc-arm-none-eabi-7-2017-q4-major   7.2.1  50 https://developer.arm.com/-/media/Files/downloads/gnu-rm/7-2017q4/gcc-arm-none-eabi-7-2017-q4-major-linux.tar.bz2"
    "gcc-arm-none-eabi-8-2018-q4-major   8.2.1  60 https://developer.arm.com/-/media/Files/downloads/gnu-rm/8-2018q4/gcc-arm-none-eabi-8-2018-q4-major-linux.tar.bz2"
    "gcc-arm-none-eabi-9-2019-q4-major   9.2.1  70 https://developer.arm.com/-/media/Files/downloads/gnu-rm/9-2019q4/gcc-arm-none-eabi-9-2019-q4-major-x86_64-linux.tar.bz2"
    "gcc-arm-none-eabi-10-2020-q4-major 10.2.1  80 https://developer.arm.com/-/media/Files/downloads/gnu-rm/10-2020q4/gcc-arm-none-eabi-10-2020-q4-major-x86_64-linux.tar.bz2"
)

TOOLS=(g++ objcopy ar as ld objdump size)

pushd /opt/ > /dev/null

for entry in "${COMPILERS[@]}"; do
    read -r name gcc_ver priority url <<< "$entry"
    echo "Downloading and extracting ${name}"
    wget -qO- "${url}" | tar -xj

    alt_args=(
        --install /bin/arm-none-eabi-gcc arm-none-eabi-gcc
        "/opt/${name}/bin/arm-none-eabi-gcc-${gcc_ver}" "${priority}"
    )
    for tool in "${TOOLS[@]}"; do
        alt_args+=(
            --slave "/bin/arm-none-eabi-${tool}" "arm-none-eabi-${tool}"
            "/opt/${name}/bin/arm-none-eabi-${tool}"
        )
    done
    update-alternatives "${alt_args[@]}"
done

popd > /dev/null
echo "Done. Use 'update-alternatives --config arm-none-eabi-gcc' to switch versions."
