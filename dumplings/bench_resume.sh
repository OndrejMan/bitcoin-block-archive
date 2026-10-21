#!/usr/bin/env bash
# Measure the peak memory of a Dumplings resume over an existing Scanner/.
#
# A regtest Core stands in for mainnet. It sits below the Scanner's
# LastProcessedBlockHeight, so Dumplings loads its state and stops without
# scanning a block: the number reported is the cost of resuming alone.
#
#   bench_resume.sh <dumplings-checkout> <scanner-dir> [label]
set -euo pipefail

src=$(realpath "$1")
scanner=$(realpath "$2")
label=${3:-$(git -C "$src" rev-parse --short HEAD)}
sdk=mcr.microsoft.com/dotnet/sdk:8.0
core=dumplings-bench-core-$$
nuget=${DUMPLINGS_NUGET_CACHE:-$HOME/.cache/dumplings-nuget}
out=$(mktemp -d)
mkdir -p "$nuget"

cleanup() {
    docker rm -f "$core" >/dev/null 2>&1 || true
    rm -rf "$out"
}
trap cleanup EXIT

# The SDK's first-run setup needs a writable home, so the build runs as root
# in the container and hands the output back to the caller.
docker run --rm -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
    -v "$src":/src:ro -v "$out":/out -v "$nuget":/root/.nuget/packages \
    "$sdk" sh -c "cp -r /src /build && dotnet publish /build/Dumplings.Cli -c Release -o /out -v q \
        && chown -R $(id -u):$(id -g) /out /root/.nuget/packages" >/dev/null

docker run -d --name "$core" bitcoin/bitcoin:29.1-alpine \
    -regtest -server -txindex=1 -rpcport=8332 -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 \
    -rpcuser=user -rpcpassword=password -printtoconsole >/dev/null
for _ in $(seq 60); do
    docker exec "$core" bitcoin-cli -regtest -rpcport=8332 -rpcuser=user -rpcpassword=password \
        getblockcount >/dev/null 2>&1 && break
    sleep 1
done

# The Scanner is mounted read-write because a patched Dumplings may repair a
# torn last line; the caller passes a copy it is willing to have changed.
docker run --rm --network "container:$core" --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -v "$out":/app:ro -v "$scanner":/work/Scanner -w /work \
    "$sdk" sh -c '
        start=$(date +%s)
        dotnet /app/Dumplings.Cli.dll sync --rpcuser=user --rpcpassword=password >/tmp/log 2>&1
        status=$?
        end=$(date +%s)
        peak=$(cat /sys/fs/cgroup/memory.peak)
        tail -3 /tmp/log >&2
        echo "status=$status seconds=$((end - start)) peak_mib=$((peak / 1048576))"
    ' | sed "s/^/$label /"
