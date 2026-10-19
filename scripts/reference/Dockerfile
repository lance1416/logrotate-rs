# Ubuntu's current LTS; resolve package revisions from its signed repositories.
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    autoconf automake build-essential gzip libpopt-dev pkg-config python3 \
    && rm -rf /var/lib/apt/lists/*
COPY source.tar version.txt upstream-sha.txt /opt/reference/
RUN mkdir /opt/upstream && python3 -c \
    "import tarfile; tarfile.open('/opt/reference/source.tar').extractall('/opt/upstream', filter='data')" \
    && cp /opt/reference/version.txt /opt/upstream/.tarball-version
WORKDIR /opt/upstream
RUN ./autogen.sh && ./configure --without-acl --without-selinux \
    --with-state-file-path=/tmp/reference-default-state-DO-NOT-USE && make -j2
COPY run.py rename_gate.c /opt/reference/
RUN cc -shared -fPIC -Wall -Wextra -Werror -o /opt/reference/rename_gate.so \
    /opt/reference/rename_gate.c -ldl \
    && { cat /opt/reference/upstream-sha.txt; cat /etc/os-release; \
    dpkg-query -W; cc --version; autoconf --version; automake --version; \
    python3 --version; gzip --version; /opt/upstream/logrotate --version; \
    sha256sum /opt/reference/source.tar /opt/upstream/logrotate; \
    ldd /opt/upstream/logrotate; } > /opt/reference/build-metadata.txt 2>&1 \
    && useradd --uid 10001 --create-home reference
USER reference
WORKDIR /tmp
CMD ["python3", "/opt/reference/run.py", "--binary", "/opt/upstream/logrotate", "--output", "/tmp/evidence", "--rename-gate", "/opt/reference/rename_gate.so"]
