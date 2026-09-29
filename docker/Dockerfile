# Official image pinned by cibuildwheel 4.2.1 (2026.09.05-1).
# https://github.com/pypa/cibuildwheel/blob/v4.2.1/cibuildwheel/resources/pinned_docker_images.cfg
ARG MANYLINUX_IMAGE=quay.io/pypa/manylinux_2_28_x86_64@sha256:53390351aeb4688114b02c36a23b3e6ce1166ee9b7afc5df1a4f776354fc764c
FROM ${MANYLINUX_IMAGE}

RUN dnf install --assumeyes clang && dnf clean all
RUN curl --fail --location --retry 3 --output /tmp/bend.tar.gz \
      https://github.com/bendlang/bend/releases/download/v2.0.28/bend-2.0.28-linux-x64.tar.gz \
    && echo '22bb6d5f6bce8ae2c5b340371fedddcbd90edc07a48b6e2b351a944c4558a3eb  /tmp/bend.tar.gz' | sha256sum --check \
    && tar --warning=no-unknown-keyword -xzf /tmp/bend.tar.gz -C /opt \
    && rm /tmp/bend.tar.gz \
    && /opt/bend/bin/bend version

ENV CC=clang BEND=/opt/bend/bin/bend
COPY scripts/build-manylinux.sh /usr/local/bin/build-manylinux
WORKDIR /io
ENTRYPOINT ["bash", "/usr/local/bin/build-manylinux"]
