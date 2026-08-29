#!/bin/sh
set -eu

# The archiver deliberately uses s5cmd's profile interface. Convert common
# container secrets into that short-lived credentials file so callers never
# need to mount ~/.aws into the image.
access_key="${S3_ACCESS_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}"
secret_key="${S3_SECRET_ACCESS_KEY:-${AWS_SECRET_ACCESS_KEY:-}}"
profile="${S3_PROFILE:-coinjoin}"
credentials_file="${S3_CREDENTIALS_FILE:-/tmp/bitcoin-block-archive-credentials}"
bitcoin_datadir="${BITCOIN_DATADIR:-/bitcoin}"

if [ -n "${access_key}" ] || [ -n "${secret_key}" ]; then
    if [ -z "${access_key}" ] || [ -z "${secret_key}" ]; then
        echo "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together" >&2
        exit 2
    fi
    umask 077
    printf '[%s]\naws_access_key_id = %s\naws_secret_access_key = %s\n' \
        "${profile}" "${access_key}" "${secret_key}" >"${credentials_file}"
    set -- --credentials "${credentials_file}" --profile "${profile}" "$@"
fi

if [ -n "${S3_ENDPOINT_URL:-}" ]; then
    set -- --endpoint "${S3_ENDPOINT_URL}" "$@"
fi
if [ -n "${S3_DESTINATION:-}" ]; then
    set -- --destination "${S3_DESTINATION}" "$@"
fi

# Compose uses a dedicated Bitcoin Core container. Its cookie remains on the
# shared read-only datadir, while this tiny client wrapper targets its service
# name instead of the archiver container's own localhost.
if [ -n "${BITCOIN_RPC_HOST:-}" ]; then
    export BITCOIN_DATADIR="${bitcoin_datadir}"
    export BITCOIN_RPC_HOST
    export BITCOIN_RPC_PORT="${BITCOIN_RPC_PORT:-8332}"
    cat > /tmp/bitcoin-cli-remote <<'SH'
#!/bin/sh
set -eu
exec bitcoin-cli -datadir="${BITCOIN_DATADIR}" \
  -rpcconnect="${BITCOIN_RPC_HOST}" -rpcport="${BITCOIN_RPC_PORT}" "$@"
SH
    chmod 0700 /tmp/bitcoin-cli-remote
    set -- --bitcoin-cli /tmp/bitcoin-cli-remote \
        --bitcoin-datadir "${bitcoin_datadir}" "$@"
fi

exec bitcoin-block-archive "$@"
