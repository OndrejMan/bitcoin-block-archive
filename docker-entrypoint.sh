#!/bin/sh
set -eu

# The archiver reads its other settings (S3_ENDPOINT_URL, S3_DESTINATION,
# BITCOIN_RPC_HOST, ...) from the environment itself. Only the secrets need
# this wrapper: s5cmd takes them from a credentials file, so convert common
# container secrets into a short-lived one, or use a read-only mounted file.
profile="${S3_PROFILE:-coinjoin}"
if [ -n "${S3_CREDENTIALS_FILE:-}" ]; then
    if [ ! -f "$S3_CREDENTIALS_FILE" ] || [ ! -r "$S3_CREDENTIALS_FILE" ]; then
        echo "S3_CREDENTIALS_FILE must name a readable regular file" >&2
        exit 2
    fi
    exec bitcoin-block-archive --credentials "$S3_CREDENTIALS_FILE" --profile "$profile" "$@"
fi

access_key="${S3_ACCESS_KEY_ID:-}"
secret_key="${S3_SECRET_ACCESS_KEY:-}"
credentials_names="S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY"
if [ -z "${access_key}" ] && [ -z "${secret_key}" ]; then
    access_key="${AWS_ACCESS_KEY_ID:-}"
    secret_key="${AWS_SECRET_ACCESS_KEY:-}"
    credentials_names="AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
fi
credentials_file="${TMPDIR:-/tmp}/bitcoin-block-archive-credentials"

if [ -n "${access_key}" ] || [ -n "${secret_key}" ]; then
    if [ -z "${access_key}" ] || [ -z "${secret_key}" ]; then
        echo "${credentials_names} must be set together" >&2
        exit 2
    fi
    umask 077
    printf '[%s]\naws_access_key_id = %s\naws_secret_access_key = %s\n' \
        "${profile}" "${access_key}" "${secret_key}" >"${credentials_file}"
    set -- --credentials "${credentials_file}" --profile "${profile}" "$@"
fi

exec bitcoin-block-archive "$@"
