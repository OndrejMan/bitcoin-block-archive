#!/usr/bin/env bash
# Local end-to-end test of the Dumplings service: regtest Core with txindex,
# MinIO for S3, three passes. Passes when each pass publishes the new height,
# latest.json follows it, the snapshot before it survives one more pass for
# readers still downloading it, older ones are gone, and block-hashes.json
# matches Core.
#
#   test_service.sh <dumplings-checkout>
set -euo pipefail

src=$(realpath "$1")
here=$(dirname "$(realpath "$0")")
tag=dumplings-service:test-$$
net=dumplings-test-$$
core=$net-core
minio=$net-minio
work=$(mktemp -d)
mkdir -p "$work/bitcoin" "$work/data"
export AWS_ACCESS_KEY_ID=dumplings AWS_SECRET_ACCESS_KEY=dumplings-secret

cleanup() {
    docker rm -f "$core" "$minio" >/dev/null 2>&1 || true
    docker network rm "$net" >/dev/null 2>&1 || true
    docker image rm "$tag" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

cli() {
    docker exec -u bitcoin "$core" bitcoin-cli -regtest -datadir=/bitcoin -rpcport=8332 "$@"
}

s3() {
    docker run --rm --network "$net" -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY --entrypoint s5cmd "$tag" \
        --endpoint-url "http://$minio:9000" "$@"
}

service_pass() {
    docker run --rm --network "container:$core" --user "$(id -u):$(id -g)" \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e S3_ENDPOINT_URL="http://$minio:9000" \
        -e DUMPLINGS_S3_DESTINATION=s3://archive/dumplings -e DUMPLINGS_INTERVAL=0 \
        -e BITCOIN_RPC_COOKIE=/bitcoin/regtest/.cookie \
        -v "$work/bitcoin":/bitcoin:ro -v "$work/data":/data "$tag"
}

latest_height() {
    s3 cat s3://archive/dumplings/latest.json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["last_processed_height"])'
}

docker build -q --build-arg DUMPLINGS_COMMIT="$(git -C "$src" rev-parse HEAD)" \
    -t "$tag" "$src" >/dev/null
docker network create "$net" >/dev/null
docker run -d --name "$minio" --network "$net" -e MINIO_ROOT_USER="$AWS_ACCESS_KEY_ID" \
    -e MINIO_ROOT_PASSWORD="$AWS_SECRET_ACCESS_KEY" pgsty/minio:RELEASE.2026-08-04T00-00-00Z server /data >/dev/null
docker run -d --name "$core" --network "$net" -e UID="$(id -u)" -e GID="$(id -g)" -e BITCOIN_DATA=/bitcoin \
    -v "$work/bitcoin":/bitcoin bitcoin/bitcoin:29.1-alpine \
    -regtest -server -txindex=1 -blocksxor=0 -rpcport=8332 -rpcbind=127.0.0.1 -rpcallowip=127.0.0.1 >/dev/null
for _ in $(seq 60); do cli getblockcount >/dev/null 2>&1 && s3 ls >/dev/null 2>&1 && break; sleep 1; done
s3 mb s3://archive >/dev/null
cli createwallet service >/dev/null
cli generatetoaddress 300 "$(cli getnewaddress)" >/dev/null

# A small seed whose height sits just above genesis, so the pass scans real blocks.
python3 -I "$here/gen_scanner.py" "$work/data/Scanner" --scale 0.0005 --height 1

service_pass >"$work/first.log" 2>&1 || { tail -20 "$work/first.log"; exit 1; }
first=$(latest_height)
echo "first pass published height $first (tip $(cli getblockcount))"

cli generatetoaddress 50 "$(cli getnewaddress)" >/dev/null
service_pass >"$work/second.log" 2>&1 || { tail -20 "$work/second.log"; exit 1; }
second=$(latest_height)
echo "second pass published height $second (tip $(cli getblockcount))"

failed=0
listing=$(s3 ls "s3://archive/dumplings/*")
grep -Eq "(^|[[:space:]/])$first/" <<<"$listing" || { echo "FAIL: snapshot $first was deleted while it was the previous one"; failed=1; }

cli generatetoaddress 20 "$(cli getnewaddress)" >/dev/null
service_pass >"$work/third.log" 2>&1 || { tail -20 "$work/third.log"; exit 1; }
third=$(latest_height)
echo "third pass published height $third (tip $(cli getblockcount))"

listing=$(s3 ls "s3://archive/dumplings/*")
[ "$first" -eq 299 ] || { echo "FAIL: first height $first, expected 299"; failed=1; }
[ "$second" -eq 349 ] || { echo "FAIL: second height $second, expected 349"; failed=1; }
[ "$third" -eq 369 ] || { echo "FAIL: third height $third, expected 369"; failed=1; }
grep -q "$third/LastProcessedBlockHeight.txt" <<<"$listing" || { echo "FAIL: snapshot $third missing"; failed=1; }
grep -q "$third/Checkpoint.txt" <<<"$listing" || { echo "FAIL: checkpoint missing from snapshot $third"; failed=1; }
grep -Eq "(^|[[:space:]/])$second/" <<<"$listing" || { echo "FAIL: previous snapshot $second was deleted"; failed=1; }
if grep -Eq "(^|[[:space:]/])$first/" <<<"$listing"; then
    echo "FAIL: snapshot $first before the previous one was not deleted"
    failed=1
fi
published=$(s3 cat s3://archive/dumplings/block-hashes.json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["hashes"]["360"])')
[ "$published" = "$(cli getblockhash 360)" ] || { echo "FAIL: block-hashes.json disagrees with Core at 360"; failed=1; }
echo "block-hashes.json matches Core"
[ "$failed" = 0 ] && echo PASS
exit "$failed"
