FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN echo "deb-src http://archive.ubuntu.com/ubuntu/ jammy-security main restricted universe multiverse" >> /etc/apt/sources.list


RUN apt-get update && \
  apt-get install -y \
  bison \
  flex \
  libfdt-dev \
  libglib2.0-dev \
  libslirp-dev \
  zlib1g-dev \
  build-essential \
  ca-certificates \
  cmake \
  ethtool \
  g++ \
  gcc-arm-none-eabi \
  git \
  gdb-multiarch \
  libpixman-1-dev \
  python3-pip \
  python3-venv \
  python-tk \
  tcpdump \
  vim \
  wget \
  meson \
  ninja-build \
  pkg-config \
  python3 && \
  apt-get clean && \
  apt-get autoclean -y && \
  rm -rf /var/lib/apt/lists/*


WORKDIR /root
ADD . ./halucinator
WORKDIR /root/halucinator
RUN pip install --upgrade pip "setuptools>=62,<81" wheel
RUN pip install cffi
RUN pip install "z3-solver>=4.8.5.0,<4.13"
RUN pip install -e deps/avatar2/
RUN pip install --no-build-isolation "pyvex==8.20.7.27"
RUN pip install --no-build-isolation -r src/requirements.txt
RUN pip install -e src

RUN mkdir -p deps/build-qemu/arm-softmmu
RUN mkdir -p deps/build-qemu/aarch64-softmmu
RUN mkdir -p deps/build-qemu/ppc-softmmu
RUN mkdir -p deps/build-qemu/ppc64-softmmu
RUN mkdir -p deps/build-qemu/mips-softmmu
# RUN pip install meson

WORKDIR /root/halucinator/deps/build-qemu/arm-softmmu
RUN /root/halucinator/deps/avatar-qemu/configure --target-list=arm-softmmu
RUN make all -j`nproc`

WORKDIR /root/halucinator/deps/build-qemu/aarch64-softmmu
RUN /root/halucinator/deps/avatar-qemu/configure --target-list=aarch64-softmmu
RUN make all -j`nproc`

WORKDIR /root/halucinator/deps/build-qemu/ppc-softmmu
RUN /root/halucinator/deps/avatar-qemu/configure --target-list=ppc-softmmu
RUN make all -j`nproc`

WORKDIR /root/halucinator/deps/build-qemu/mips-softmmu
RUN /root/halucinator/deps/avatar-qemu/configure --target-list=mips-softmmu
RUN make all -j`nproc`

WORKDIR /root/halucinator/deps/build-qemu/ppc64-softmmu
RUN /root/halucinator/deps/avatar-qemu/configure --target-list=ppc64-softmmu
RUN make all -j`nproc`

WORKDIR  /root/halucinator
RUN ln -s -T /usr/bin/gdb-multiarch /usr/bin/arm-none-eabi-gdb


CMD /root/halucinator
