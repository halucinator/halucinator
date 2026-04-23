ARG UBUNTU_VERSION=22.04
FROM ubuntu:${UBUNTU_VERSION}

ENV DEBIAN_FRONTEND=noninteractive

# Detect codename for deb-src
RUN CODENAME=$(grep VERSION_CODENAME /etc/os-release | cut -d= -f2) && \
  echo "deb-src http://archive.ubuntu.com/ubuntu/ ${CODENAME} main restricted universe multiverse" >> /etc/apt/sources.list && \
  echo "deb-src http://archive.ubuntu.com/ubuntu/ ${CODENAME}-security main restricted universe multiverse" >> /etc/apt/sources.list

RUN apt-get update && \
  (apt-get build-dep -y qemu || true) && \
  apt-get install -y \
  build-essential \
  ca-certificates \
  cmake \
  ethtool \
  g++ \
  gcc-arm-none-eabi \
  git \
  gdb-multiarch \
  libaio-dev \
  libglib2.0-dev \
  libpixman-1-dev \
  pkg-config \
  python3-pip \
  python3-venv \
  python3-tk \
  sudo \
  tcpdump \
  vim \
  wget \
  ninja-build && \
  apt-get clean && \
  apt-get autoclean -y && \
  rm -rf /var/lib/apt/lists/*


WORKDIR /root
ADD . ./halucinator
WORKDIR /root/halucinator

# Install Python packages
# Use --break-system-packages on 24.04+ (PEP 668), no-op on 22.04
RUN PIP_FLAGS=""; pip install --break-system-packages --help >/dev/null 2>&1 && PIP_FLAGS="--break-system-packages"; \
    pip install $PIP_FLAGS -e deps/avatar2/ && \
    pip install $PIP_FLAGS -r src/requirements.txt && \
    pip install $PIP_FLAGS -e src

# Build QEMU for all supported architectures
RUN ./build_qemu.sh

WORKDIR  /root/halucinator

# Symlink so VSCode extensions can find halucinator at /halucinator/
RUN ln -s /root/halucinator /halucinator

# Generate bpdata.json for VSCode extensions
RUN python3 extra_tools/parse_bp_handlers.py -s src/halucinator -o bpdata.json

# Set QEMU environment variables
ENV HALUCINATOR_QEMU_ARM="/root/halucinator/deps/build-qemu/arm-softmmu/qemu-system-arm"
ENV HALUCINATOR_QEMU_ARM64="/root/halucinator/deps/build-qemu/aarch64-softmmu/qemu-system-aarch64"
ENV HALUCINATOR_QEMU_PPC="/root/halucinator/deps/build-qemu/ppc-softmmu/qemu-system-ppc"
ENV HALUCINATOR_QEMU_PPC64="/root/halucinator/deps/build-qemu/ppc64-softmmu/qemu-system-ppc64"
ENV HALUCINATOR_QEMU_MIPS="/root/halucinator/deps/build-qemu/mips-softmmu/qemu-system-mips"

# Target directory for user projects
ENV TARGET="/home/haluser/project"

# Create haluser with sudo access for Docker workflows
RUN useradd -u 20000 -m -s /bin/bash haluser && \
    echo "haluser:password" | chpasswd && \
    echo "haluser    ALL=(ALL:ALL) ALL" >> /etc/sudoers && \
    usermod -aG sudo haluser && \
    echo "PS1='halucinator-docker:\w # '" >> /home/haluser/.bashrc

# Make halucinator accessible to haluser
RUN chmod -R a+rX /root /root/halucinator

# Copy demo files to user home
RUN cp -r demo /home/haluser/demo && chown -R haluser:haluser /home/haluser/demo

USER haluser
WORKDIR /home/haluser
