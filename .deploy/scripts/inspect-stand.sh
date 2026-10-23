#!/bin/bash
# Inspection of the currently running open5gs-compose stack (test stand 192.168.0.9)
# Purpose: find out where mongodb/openbao data actually lives before recreating containers.
set -u
PROJECT_DIR="$HOME/open5gs-compose"

echo "=== docker ps (mongo/bao/webui/hss) ==="
docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}' | grep -Ei 'mongo|bao|webui|hss' || true

echo ""
echo "=== mongodb container config ==="
docker inspect open5gs-compose-mongodb-1 --format 'IMAGE={{.Config.Image}}' 2>/dev/null || echo "inspect failed"
docker inspect open5gs-compose-mongodb-1 --format 'PIDMODE={{.HostConfig.PidMode}} CMD={{json .Config.Cmd}} ENTRY={{json .Config.Entrypoint}}' 2>/dev/null || true
echo "--- mongodb mounts ---"
docker inspect open5gs-compose-mongodb-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}' 2>/dev/null || true

echo ""
echo "=== openbao container config ==="
docker inspect open5gs-compose-openbao-1 --format 'IMAGE={{.Config.Image}}' 2>/dev/null || echo "inspect failed"
echo "--- openbao mounts ---"
docker inspect open5gs-compose-openbao-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}' 2>/dev/null || true

echo ""
echo "=== local-data dir on host ==="
ls -la "$PROJECT_DIR/.deploy/docker/local-data/" 2>/dev/null || echo "local-data dir missing"

echo ""
echo "=== local-data/mongodb contents ==="
ls -la "$PROJECT_DIR/.deploy/docker/local-data/mongodb/" 2>/dev/null | head -20 || echo "mongodb data dir missing"

echo ""
echo "=== local-data/openbao contents ==="
ls -la "$PROJECT_DIR/.deploy/docker/local-data/openbao/" 2>/dev/null || echo "openbao data dir missing"
ls -la "$PROJECT_DIR/.deploy/docker/local-data/openbao/secrets/" 2>/dev/null || true
ls "$PROJECT_DIR/.deploy/docker/local-data/openbao/data/" 2>/dev/null | head || true

echo ""
echo "=== mongodb: where does mongod actually store data (inside running container) ==="
docker exec open5gs-compose-mongodb-1 sh -c 'ps aux | head -5; echo ---; ls -la /data 2>&1; echo ---; mongosh --quiet --eval "db.adminCommand({getParameter:1, dbPath:1})" 2>/dev/null || mongo --quiet --eval "db.adminCommand({getParameter:1, dbPath:1})" 2>/dev/null || echo "dbPath query failed"' 2>&1 || true

echo ""
echo "=== openbao: secrets dir inside running container ==="
docker exec open5gs-compose-openbao-1 sh -c 'ls -la /openbao/secrets/ 2>&1; ls /openbao/data 2>/dev/null | head' 2>&1 || true

echo ""
echo "=== compose project containers labels (which compose project they belong to) ==="
docker inspect open5gs-compose-mongodb-1 --format '{{index .Config.Labels "com.docker.compose.project"}} / config-hash: {{index .Config.Labels "com.docker.compose.config-hash"}}' 2>/dev/null || true
cd "$PROJECT_DIR" && docker compose -f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.encryption.yaml config --hash mongodb 2>/dev/null || true

echo ""
echo "INSPECTION DONE"
