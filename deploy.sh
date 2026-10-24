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
credentials_source=$(compose config --environment | sed -n 's/^S3_CREDENTIALS_SOURCE=//p')
if [[ -n "$credentials_source" ]]; then
    if [[ ! -f "$credentials_source" || ! -r "$credentials_source" ]]; then
        printf 'S3_CREDENTIALS_SOURCE in %s/.env must name a readable regular file.\n' "$PWD" >&2
        exit 1
    fi
else
    missing=$(compose config --environment | awk '
        /^S3_ACCESS_KEY_ID=./ { access = 1 }
        /^S3_SECRET_ACCESS_KEY=./ { secret = 1 }
        /^AWS_ACCESS_KEY_ID=./ { aws_access = 1 }
        /^AWS_SECRET_ACCESS_KEY=./ { aws_secret = 1 }
        END {
            if (!access && !secret && aws_access && aws_secret) exit
            if (!access) print "S3_ACCESS_KEY_ID"
            if (!secret) print "S3_SECRET_ACCESS_KEY"
        }
    ')
    if [[ -n "$missing" ]]; then
        printf 'Set S3_CREDENTIALS_SOURCE or fill in these values in %s/.env:\n%s\n' "$PWD" "$missing" >&2
        exit 1
    fi
fi
compose pull
mv -- "$temporary" compose.yaml
compose_file=compose.yaml

compose up -d --wait bitcoin-core
compose up -d --no-deps --force-recreate archiver dumplings
compose ps
