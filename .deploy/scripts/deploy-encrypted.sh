#!/bin/bash
# Deploy open5gs-compose with encrypted Percona Server for MongoDB (PSMDB)
# and OpenBao KMS over TLS. Run ON THE SERVER (bash ~/open5gs-compose/.deploy/scripts/deploy-encrypted.sh).
#
# Steps:
#  1. Generate TLS certificates for OpenBao (if not present)
#  2. Dump existing (unencrypted) MongoDB data, if any
#  3. Stop the stack, back up old data dirs
#  4. Re-initialize OpenBao (fresh KV v2 + master key), start with TLS
#  5. Start PSMDB with data-at-rest encryption enabled
#  6. Restore the dump into the encrypted storage
#  7. Start the whole stack
#  8. Verify: vault key, TLS, no plaintext in data files, services health
#
# NOTE: OpenBao is re-initialized on purpose (its old state only had an unused
# KV v1 secret; no data was ever encrypted with it). Old state is kept in
# .deploy/docker/local-data/openbao-old-<timestamp>/.
set -euo pipefail
cd "$(dirname "$0")/../.."
# Compose file chain: base + stand overrides + encrypted PSMDB overlay.
# The explicit -f chain keeps this script independent of COMPOSE_FILE in .env.
COMPOSE_FILES=(-f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.encryption.yaml)
dc() { docker compose "${COMPOSE_FILES[@]}" "$@"; }

DATA_DIR=".deploy/docker/local-data/mongodb"
BAO_DIR=".deploy/docker/local-data/openbao"
DUMP_DIR=".deploy/dump-migration"
STAMP=$(date +%Y%m%d-%H%M%S)

log() { echo "[deploy $(date +%H:%M:%S)] $*"; }

# Move a directory even if it is owned by root/other uid (fallback via docker).
move_dir() {
  local src="$1" dst="$2"
  if mv "$src" "$dst" 2>/dev/null; then return 0; fi
  docker run --rm --entrypoint "" \
    -v "$PWD:/w" percona/percona-server-mongodb:5.0 \
    sh -c "mv '/w/$src' '/w/$dst'"
}

# Wait until a compose service is healthy; print logs and fail otherwise.
wait_healthy() {
  local svc="$1" timeout="${2:-180}" waited=0 cid st
  cid=$(dc ps -q "$svc" 2>/dev/null || true)
  if [ -z "$cid" ]; then
    log "ERROR: no container for service '$svc'"
    return 1
  fi
  while [ "$waited" -lt "$timeout" ]; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" != "true" ]; then
      log "ERROR: '$svc' container is not running. Recent logs:"
      dc logs --tail 60 "$svc" || true
      return 1
    fi
    st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo none)
    if [ "$st" = "healthy" ]; then
      log "'$svc' is healthy"
      return 0
    fi
    if [ "$st" = "unhealthy" ]; then
      log "ERROR: '$svc' is unhealthy. Recent logs:"
      dc logs --tail 60 "$svc" || true
      return 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
  log "ERROR: timed out waiting for '$svc'. Recent logs:"
  dc logs --tail 60 "$svc" || true
  return 1
}

log "=== Step 1/8: TLS certificates for OpenBao ==="
bash .deploy/scripts/generate-certs.sh

log "=== Step 2/8: dump existing MongoDB data (if any) ==="
HAVE_DUMP=false
if [ -d "$DUMP_DIR/dump" ]; then
  HAVE_DUMP=true
  log "Existing dump found at $DUMP_DIR/dump, keeping it (no re-dump)."
elif [ -d "$DATA_DIR" ] && ls "$DATA_DIR"/WiredTiger* >/dev/null 2>&1; then
  MDB_CID=$(dc ps -q mongodb 2>/dev/null || true)
  MDB_RUNNING=false
  # "Running" is not enough: the container may be in a restart loop.
  # Only use the exec path if mongod actually answers a ping.
  if [ -n "$MDB_CID" ] && [ "$(docker inspect -f '{{.State.Running}}' "$MDB_CID" 2>/dev/null || echo false)" = "true" ] && \
     dc exec -T mongodb mongo --quiet --eval 'db.adminCommand({ping:1})' >/dev/null 2>&1; then
    MDB_RUNNING=true
  fi
  if [ "$MDB_RUNNING" = "true" ]; then
    log "mongodb is running, dumping via compose exec..."
    rm -rf "$DUMP_DIR"
    mkdir -p "$DUMP_DIR"
    dc exec -T mongodb sh -c 'rm -rf /tmp/dump && mongodump --out=/tmp/dump'
    dc cp mongodb:/tmp/dump "$DUMP_DIR/dump"
    dc exec -T -u 0 mongodb rm -rf /tmp/dump
  else
    log "mongodb is not running, starting temporary plain mongod for the dump..."
    rm -rf "$DUMP_DIR"
    mkdir -p "$DUMP_DIR"
    docker run -d --rm --name mongod-dump-temp \
      --entrypoint "" \
      -v "$PWD/$DATA_DIR:/data/db" \
      percona/percona-server-mongodb:5.0 \
      mongod --dbpath /data/db --bind_ip 127.0.0.1 --port 27017 >/dev/null
    for i in $(seq 1 60); do
      if docker exec mongod-dump-temp mongo --quiet --eval 'db.adminCommand({ping:1})' >/dev/null 2>&1; then break; fi
      sleep 2
    done
    docker exec mongod-dump-temp sh -c 'rm -rf /tmp/dump && mongodump --out=/tmp/dump'
    docker cp mongod-dump-temp:/tmp/dump "$DUMP_DIR/dump"
    docker stop mongod-dump-temp >/dev/null
  fi
  HAVE_DUMP=true
  log "Existing data dumped to $DUMP_DIR/dump ($(du -sh "$DUMP_DIR/dump" 2>/dev/null | cut -f1))"
else
  log "No existing MongoDB data found - fresh deployment."
fi

log "=== Step 3/8: stop the stack ==="
dc down --remove-orphans || true

log "=== Step 4/8: back up old data dirs ==="
if [ -d "$DATA_DIR" ]; then
  move_dir "$DATA_DIR" "${DATA_DIR}-plain-${STAMP}"
  log "Old (unencrypted) MongoDB data kept at: ${DATA_DIR}-plain-${STAMP}"
fi
if [ -d "$BAO_DIR" ]; then
  move_dir "$BAO_DIR" "${BAO_DIR}-old-${STAMP}"
  log "Old OpenBao state kept at: ${BAO_DIR}-old-${STAMP}"
fi

# Recreate the MongoDB data dir with ownership for the mongod user (uid 1001).
# Docker auto-creates missing bind-mount dirs as root:root, which makes mongod
# fail with "Attempted to create a lock file on a read-only directory".
# The PSMDB image runs as uid 1001 by default, so chown must be forced to root.
mkdir -p "$DATA_DIR"
docker run --rm --user 0:0 --entrypoint "" \
  -v "$PWD/$DATA_DIR:/data/db" \
  percona/percona-server-mongodb:5.0 \
  chown 1001:1001 /data/db
log "Data dir '$DATA_DIR' ready (owned by uid 1001)."

log "=== Step 5/8: start OpenBao (TLS + KV v2) and encrypted PSMDB ==="
dc up -d openbao mongodb
wait_healthy openbao 120
wait_healthy mongodb 240

log "=== Step 6/8: restore data dump into encrypted PSMDB ==="
if [ "$HAVE_DUMP" = "true" ] || [ -d "$DUMP_DIR/dump" ]; then
  dc cp "$DUMP_DIR/dump" mongodb:/tmp/dump
  dc exec -T mongodb mongorestore /tmp/dump
  dc exec -T -u 0 mongodb rm -rf /tmp/dump
  log "Data restored into encrypted storage."
else
  log "No dump to restore (fresh deployment)."
fi

# Create the webui admin account (previously done via docker-entrypoint-initdb.d,
# which is incompatible with encryption - see docker-compose.encryption.yaml note).
log "Running init.js (webui admin account)..."
dc exec -T mongodb mongo --quiet /opt/init/init.js
log "init.js done."

log "=== Step 7/8: start the whole stack ==="
dc up -d

log "=== Step 8/8: verification ==="
echo "--- OpenBao: secrets engines ---"
dc exec -T openbao sh -c \
  'export BAO_ADDR=http://127.0.0.1:8201 BAO_TOKEN=$(cat /openbao/secrets/root-token); bao secrets list'
echo "--- OpenBao: PSMDB master key present ---"
dc exec -T openbao sh -c \
  'export BAO_ADDR=http://127.0.0.1:8201 BAO_TOKEN=$(cat /openbao/secrets/root-token); bao kv get -field=value secret/psmdb-encryption >/dev/null && echo "master key: OK"'
echo "--- mongod log: vault/encryption lines ---"
dc logs mongodb 2>&1 | grep -iE 'vault|encrypt' | tail -10 || echo "(no vault/encryption lines found in logs)"
echo "--- TLS from mongodb container to openbao:8200 ---"
dc exec -T mongodb sh -c \
  'openssl s_client -CAfile /etc/mongodb-tls/ca.crt -connect openbao:8200 </dev/null 2>/dev/null | grep -E "Verify return code|subject=" || echo "TLS check failed"'
echo "--- data files must not contain plaintext (IMSI patterns) ---"
WT_FILE=$(ls .deploy/docker/local-data/mongodb/collection-*.wt 2>/dev/null | head -1 || true)
if [ -n "$WT_FILE" ]; then
  if grep -aqE '99999[0-9]{10,}' "$WT_FILE"; then
    log "WARNING: possible plaintext IMSI found in $WT_FILE"
  else
    log "OK: no plaintext IMSI patterns in $WT_FILE"
  fi
else
  log "(no collection .wt files yet)"
fi
echo "--- databases ---"
dc exec -T mongodb mongo --quiet --eval \
  'db.adminCommand({listDatabases:1}).databases.forEach(function(d){print(d.name)})' || true
echo "--- services ---"
dc ps --format 'table {{.Service}}\t{{.Status}}'

log "=================================================="
log "DEPLOYMENT COMPLETE"
log "Backups: ${DATA_DIR}-plain-${STAMP} (old plain data), ${BAO_DIR}-old-${STAMP} (old openbao)"
log "Dump kept at: $DUMP_DIR/dump"
log "=================================================="
