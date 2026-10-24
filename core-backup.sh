#!/usr/bin/env bash
# Run from the deployment directory, optionally from a daily host cron job.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
exec 9>.core-backup.lock
flock -n 9 || { echo 'Another Core backup is running' >&2; exit 1; }

compose() {
    docker compose --project-name bitcoin-block-archive --profile archive \
        --profile dumplings --profile backup "$@"
}
step() {
    compose run --rm --no-deps core-backup \
        --block-dir /bitcoin/blocks --state-dir /state --core-backup "$1"
}
running=$(compose ps --status running --services)
if [[ ${1:-backup} == restore ]]; then
    [[ -z "$running" ]] || { echo 'Stop all services before restoring Core' >&2; exit 1; }
    # Read-only source in backup, writable destination only in this explicit service.
    compose run --rm --no-deps core-restore \
        --block-dir /bitcoin/blocks --state-dir /state --core-backup restore
    exit
fi
[[ ${1:-backup} == backup && $# -le 1 ]] || { echo 'Usage: core-backup.sh [backup|restore]' >&2; exit 2; }
grep -qx bitcoin-core <<<"$running" || { echo 'Core must be running for backup preflight' >&2; exit 1; }

# Refuse unpruned, still-syncing, insufficiently pruned or oversized nodes
# before stopping any service. Missing settings/RPC also fail closed.
if step check; then
    :
else
    status=$?
    if [[ $status == 3 ]]; then
        echo 'Core snapshot skipped: pruning/synchronization/size checks did not pass'
        exit 0
    fi
    exit "$status"
fi

consumers=()
for service in archiver dumplings; do
    if grep -qx "$service" <<<"$running"; then consumers+=("$service"); fi
done
core_stopped=0
consumers_stopped=0
resume() {
    local result=0
    if (( core_stopped )); then
        compose start --wait --wait-timeout 600 bitcoin-core || result=1
        if (( result == 0 )); then core_stopped=0; fi
    fi
    if (( consumers_stopped && ! core_stopped )); then
        if compose start "${consumers[@]}"; then consumers_stopped=0; else result=1; fi
    fi
    return "$result"
}
cleanup() {
    local result=$?
    trap - EXIT
    resume || result=1
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if (( ${#consumers[@]} )); then
    consumers_stopped=1
    compose stop "${consumers[@]}"
fi
# Repeat the eligibility check after consumers stopped, before stopping Core.
step prepare
core_id=$(compose ps -q bitcoin-core)
[[ -n "$core_id" ]] || { echo 'Missing Core container' >&2; exit 1; }
core_stopped=1
compose stop bitcoin-core
state=$(docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Running}}' "$core_id")
[[ "$state" == '0 false false' ]] || { echo 'Core did not exit cleanly; snapshot refused' >&2; exit 1; }
step create
# Network upload runs after Core and its consumers resume.
resume
step upload
