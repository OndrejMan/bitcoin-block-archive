# Runtime image for scheduled archive passes. The Bitcoin Core image already
# contains bitcoin-cli plus every shared library it needs; add Python and
# s5cmd instead of copying a single potentially incompatible binary out of it.
FROM bitcoin/bitcoin:29.1

USER root

ARG S5CMD_VERSION=2.3.0
ARG TARGETARCH

WORKDIR /app

RUN set -eux; \
    apt-get update; \
    apt-get install --yes --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      python3 \
      python3-venv \
      tar; \
    rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH:-$(uname -m)}" in \
      amd64|x86_64) s5arch=64bit ;; \
      arm64|aarch64) s5arch=ARM64 ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/s5cmd.tar.gz \
      "https://github.com/peak/s5cmd/releases/download/v${S5CMD_VERSION}/s5cmd_${S5CMD_VERSION}_Linux-${s5arch}.tar.gz"; \
    tar -xzf /tmp/s5cmd.tar.gz -C /usr/local/bin s5cmd; \
    rm /tmp/s5cmd.tar.gz; \
    s5cmd version

COPY pyproject.toml README.md ./
COPY src ./src
COPY docker-entrypoint.sh /usr/local/bin/bitcoin-block-archive-entrypoint
RUN python3 -m venv /opt/bitcoin-block-archive-venv \
    && /opt/bitcoin-block-archive-venv/bin/pip install --no-cache-dir --no-deps . \
    && chmod 0755 /usr/local/bin/bitcoin-block-archive-entrypoint

ENV PATH="/opt/bitcoin-block-archive-venv/bin:${PATH}"

USER bitcoin
ENTRYPOINT ["bitcoin-block-archive-entrypoint"]
