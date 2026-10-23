#!/bin/bash
# One-off helper: re-create the migration dump from a backed-up (plain) MongoDB
# data directory using a temporary mongod container. Used after the original
# dump was lost. Run ON THE SERVER from anywhere.
# Usage: bash .deploy/scripts/redump-from-backup.sh [backup-dir-name]
set -euo pipefail
cd "$(dirname "$0")/../.."

SRC_NAME="${1:-mongodb-plain-20261020-143747}"
SRC=".deploy/docker/local-data/$SRC_NAME"
DUMP_DIR=".deploy/dump-migration"

[ -d "$SRC" ] || { echo "ERROR: source dir not found: $SRC"; exit 1; }
echo "Source: $SRC ($(du -sh "$SRC" | cut -f1))"

docker rm -f mongod-dump-temp >/dev/null 2>&1 || true
docker run -d --rm --name mongod-dump-temp --entrypoint "" \
  -v "$PWD/$SRC:/data/db" \
  percona/percona-server-mongodb:5.0 \
  mongod --dbpath /data/db --bind_ip 127.0.0.1 --port 27017 >/dev/null

echo "Waiting for temporary mongod..."
for i in $(seq 1 60); do
  if docker exec mongod-dump-temp mongo --quiet --eval 'db.adminCommand({ping:1})' >/dev/null 2>&1; then
    echo "temporary mongod is ready"; break
  fi
  sleep 2
done

docker exec mongod-dump-temp sh -c 'rm -rf /tmp/dump && mongodump --out=/tmp/dump'

mkdir -p "$DUMP_DIR"
rm -rf "$DUMP_DIR/dump"
docker cp mongod-dump-temp:/tmp/dump "$DUMP_DIR/dump"
docker stop mongod-dump-temp >/dev/null

echo "Dump created at $DUMP_DIR/dump ($(du -sh "$DUMP_DIR/dump" | cut -f1)):"
ls "$DUMP_DIR/dump"
