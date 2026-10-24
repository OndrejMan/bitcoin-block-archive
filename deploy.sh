#!/usr/bin/env bash
set -euo pipefail

deploy_dir=${1:-"$HOME/bitcoin-block-archive"}
source_url=https://raw.githubusercontent.com/OndrejMan/bitcoin-block-archive/main

command -v curl >/dev/null
docker compose version >/dev/null
mkdir -p -- "$deploy_dir"
cd -- "$deploy_dir"
temporary=$(mktemp .deploy.XXXXXX)
trap 'rm -f -- "$temporary"' EXIT

if [[ ! -e .env ]]; then
    curl -fsSL "$source_url/.env.example" -o "$temporary"
    mv -- "$temporary" .env
    printf 'Created %s/.env. Fill in S3 credentials and the data disk settings, then run this script again.\n' "$PWD"
    exit 1
fi

curl -fsSL "$source_url/compose.yaml" -o "$temporary"
compose_file=$temporary
compose() {
    docker compose --project-name bitcoin-block-archive --project-directory "$PWD" \
        --env-file .env --file "$compose_file" \
        --profile archive --profile dumplings "$@"
}

compose config --quiet
compose pull
mv -- "$temporary" compose.yaml
compose_file=compose.yaml

compose up -d --wait bitcoin-core
compose up -d --no-deps --force-recreate archiver dumplings
compose ps
