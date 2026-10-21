#!/usr/bin/env bash
# Scan real blocks with a Dumplings build and kill it halfway through.
#
# A regtest chain of a few hundred blocks stands in for mainnet; the Scanner
# starts just above genesis so Dumplings walks every block over RPC
# (`getblock <hash> 3`). The first run is killed after a few seconds, the
# second resumes. Passes when the second run reaches the tip and the
# checkpoint agrees with LastProcessedBlockHeight.txt.
#
#   smoke_scan.sh <dumplings-checkout> [blocks]
set -euo pipefail

src=$(realpath "$1")
blocks=${2:-400}
here=$(dirname "$(realpath "$0")")
sdk=mcr.microsoft.com/dotnet/sdk:8.0
core=dumplings-smoke-core-$$
nuget=${DUMPLINGS_NUGET_CACHE:-$HOME/.cache/dumplings-nuget}
work=$(mktemp -d)
mkdir -p "$nuget" "$work/app"

cleanup() {
    docker rm -f "$core" "$core-scan" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

cli() {
    docker exec "$core" bitcoin-cli -regtest -rpcport=8332 -rpcuser=user -rpcpassword=password "$@"
}

docker run --rm -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
    -v "$src":/src:ro -v "$work/app":/out -v "$nuget":/root/.nuget/packages \
    "$sdk" sh -c "cp -r /src /build && dotnet publish /build/Dumplings.Cli -c Release -o /out -v q \
        && chown -R $(id -u):$(id -g) /out /root/.nuget/packages" >/dev/null

docker run -d --name "$core" bitcoin/bitcoin:29.1-alpine \
    -regtest -server -txindex=1 -rpcport=8332 -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 \
    -rpcuser=user -rpcpassword=password -fallbackfee=0.0001 >/dev/null
for _ in $(seq 60); do cli getblockcount >/dev/null 2>&1 && break; sleep 1; done
cli createwallet smoke >/dev/null
cli generatetoaddress "$blocks" "$(cli getnewaddress)" >/dev/null

python3 -I "$here/gen_scanner.py" "$work/Scanner" --scale 0.001 --height 1

scan() {
    docker run --rm --name "$core-scan" --network "container:$core" --user "$(id -u):$(id -g)" \
        -e HOME=/tmp -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -v "$work/app":/app:ro \
        -v "$work/Scanner":/work/Scanner -w /work "$sdk" \
        dotnet /app/Dumplings.Cli.dll sync --rpcuser=user --rpcpassword=password </dev/null
}

scan >"$work/first.log" 2>&1 &
sleep "${KILL_AFTER:-6}"
docker kill "$core-scan" >/dev/null 2>&1 || true
wait || true
echo "after kill: height=$(cat "$work/Scanner/LastProcessedBlockHeight.txt") checkpoint=$(head -1 "$work/Scanner/Checkpoint.txt" 2>/dev/null || echo none)"

scan >"$work/second.log" 2>&1
height=$(cat "$work/Scanner/LastProcessedBlockHeight.txt")
checkpoint=$(head -1 "$work/Scanner/Checkpoint.txt")
tip=$(cli getblockcount)
echo "after resume: height=$height checkpoint=$checkpoint tip=$tip"
# Dumplings keeps the tip block unsaved and stops there (Scanner.cs).
if [ "$height" = "$checkpoint" ] && [ "$height" -eq $((tip - 1)) ]; then
    echo PASS
else
    tail -20 "$work/second.log"
    echo FAIL
    exit 1
fi
