FROM ubuntu:22.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils binutils build-essential pkg-config ninja-build \
    bison flex gobject-introspection libgirepository1.0-dev libglib2.0-dev libdbus-1-dev \
    python3 python3-gi python3-xlib fonts-dejavu-core gir1.2-gtk-4.0 \
    squashfs-tools && rm -rf /var/lib/apt/lists/*
