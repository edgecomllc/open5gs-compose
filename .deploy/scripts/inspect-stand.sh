#!/bin/bash
# Inspection of the currently running encrypted open5gs-compose deployment.
# Purpose: find out where mongodb/openbao data actually lives before recreating containers.
# Run on the Docker host; the project root is resolved from the script location,
# container IDs are resolved via docker compose (independent of the project name).
set -u
cd "$(dirname "$0")/../.."
# Compose file chain: base + local overrides + encrypted PSMDB overlay.
COMPOSE_FILES=(-f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.encryption.yaml)
dc() { docker compose "${COMPOSE_FILES[@]}" "$@"; }

# Resolve container IDs through compose (works for any compose project name).
MDB_CID=$(dc ps -q mongodb 2>/dev/null || true)
BAO_CID=$(dc ps -q openbao 2>/dev/null || true)

is_running() {
  [ -n "${1:-}" ] && [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]
}

echo "=== docker ps (mongo/bao/webui/hss) ==="
docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}' | grep -Ei 'mongo|bao|webui|hss' || true

echo ""
echo "=== mongodb container config ==="
if [ -n "$MDB_CID" ]; then
  docker inspect "$MDB_CID" --format 'IMAGE={{.Config.Image}}'
  docker inspect "$MDB_CID" --format 'PIDMODE={{.HostConfig.PidMode}} CMD={{json .Config.Cmd}} ENTRY={{json .Config.Entrypoint}}'
  echo "--- mongodb mounts ---"
  docker inspect "$MDB_CID" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}'
else
  echo "mongodb: no container found (is the stack running?)"
fi

echo ""
echo "=== openbao container config ==="
if [ -n "$BAO_CID" ]; then
  docker inspect "$BAO_CID" --format 'IMAGE={{.Config.Image}}'
  echo "--- openbao mounts ---"
  docker inspect "$BAO_CID" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}'
else
  echo "openbao: no container found (is the stack running?)"
fi

echo ""
echo "=== local-data dir (relative to project root) ==="
ls -la .deploy/docker/local-data/ 2>/dev/null || echo "local-data dir missing"

echo ""
echo "=== local-data/mongodb contents ==="
ls -la .deploy/docker/local-data/mongodb/ 2>/dev/null | head -20 || echo "mongodb data dir missing"

echo ""
echo "=== local-data/openbao contents ==="
ls -la .deploy/docker/local-data/openbao/ 2>/dev/null || echo "openbao data dir missing"
ls -la .deploy/docker/local-data/openbao/secrets/ 2>/dev/null || true
ls .deploy/docker/local-data/openbao/data/ 2>/dev/null | head || true

echo ""
echo "=== mongodb: where does mongod actually store data (inside running container) ==="
if is_running "$MDB_CID"; then
  docker exec "$MDB_CID" sh -c 'ps aux | head -5; echo ---; ls -la /data 2>&1; echo ---; mongo --quiet --eval "db.adminCommand({getParameter:1, dbPath:1})" 2>/dev/null || echo "dbPath query failed"' 2>&1 || true
else
  echo "mongodb container is not running"
fi

echo ""
echo "=== openbao: secrets dir inside running container ==="
if is_running "$BAO_CID"; then
  docker exec "$BAO_CID" sh -c 'ls -la /openbao/secrets/ 2>&1; ls /openbao/data 2>/dev/null | head' 2>&1 || true
else
  echo "openbao container is not running"
fi

echo ""
echo "=== compose project labels (which compose project the containers belong to) ==="
if [ -n "$MDB_CID" ]; then
  docker inspect "$MDB_CID" --format '{{index .Config.Labels "com.docker.compose.project"}} / config-hash: {{index .Config.Labels "com.docker.compose.config-hash"}}' 2>/dev/null || true
fi
dc config --hash mongodb 2>/dev/null || true

echo ""
echo "INSPECTION DONE"
