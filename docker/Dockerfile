# Official image pinned by cibuildwheel 4.2.1 (2026.09.05-1).
# https://github.com/pypa/cibuildwheel/blob/v4.2.1/cibuildwheel/resources/pinned_docker_images.cfg
ARG MANYLINUX_IMAGE=quay.io/pypa/manylinux_2_28_x86_64@sha256:53390351aeb4688114b02c36a23b3e6ce1166ee9b7afc5df1a4f776354fc764c
FROM ${MANYLINUX_IMAGE}

RUN dnf --disablerepo='epel*' install --assumeyes clang && dnf clean all
RUN curl --fail --location --retry 3 --output /tmp/bend.tar.gz \
      https://github.com/bendlang/bend/releases/download/v2.0.39/bend-2.0.39-linux-x64.tar.gz \
    && echo '6cf462fd8f70b094a4950ebb709a9c4ffd8f1d1189d9cedf1fd63cd5d041c7ab  /tmp/bend.tar.gz' | sha256sum --check \
    && tar --warning=no-unknown-keyword -xzf /tmp/bend.tar.gz -C /opt \
    && rm /tmp/bend.tar.gz \
    && /opt/bend/bin/bend version

ENV CC=clang BEND=/opt/bend/bin/bend
COPY scripts/build-manylinux.sh /usr/local/bin/build-manylinux
WORKDIR /io
ENTRYPOINT ["bash", "/usr/local/bin/build-manylinux"]
