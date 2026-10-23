#!/bin/bash
# Verification of the encrypted open5gs-compose stand (run ON THE SERVER).
set -u
cd "$(dirname "$0")/../.."
# Compose file chain: base + stand overrides + encrypted PSMDB overlay.
# The explicit -f chain keeps this script independent of COMPOSE_FILE in .env.
COMPOSE_FILES=(-f docker-compose.yaml -f docker-compose.override.yaml -f docker-compose.encryption.yaml)
dc() { docker compose "${COMPOSE_FILES[@]}" "$@"; }

echo "=== 1. Container status ==="
dc ps --format '{{.Name}}\t{{.Status}}' | sort

echo
echo "=== 2. MongoDB: databases ==="
dc exec -T mongodb mongo --quiet --eval 'db.adminCommand({listDatabases:1}).databases.forEach(function(d){print("  - "+d.name)})'

echo
echo "=== 3. MongoDB: open5gs collections ==="
dc exec -T mongodb mongo --quiet open5gs --eval 'print("  subscribers: "+db.subscribers.countDocuments({})); print("  accounts: "+db.accounts.countDocuments({}))'

echo
echo "=== 4. mongod encryption status (from log) ==="
dc logs mongodb 2>&1 | grep -iE 'master encryption key|Encryption keys DB|vault' | tail -5

echo
echo "=== 5. TLS connection to OpenBao (from host) ==="
echo | openssl s_client -connect localhost:8200 -CAfile .deploy/docker/openbao/tls/ca.crt 2>/dev/null | grep -E "Verify return code|subject=" | head -3

echo
echo "=== 6. KV v2 master key secret ==="
dc exec -T openbao sh -c 'export BAO_TOKEN=$(cat /openbao/secrets/root-token); bao kv get -field=value secret/psmdb-encryption | head -c 16; echo "...(truncated)"'

echo
echo "=== 7. Plaintext check: IMSI patterns in data files ==="
dc exec -T mongodb sh -c 'HITS=$(grep -rlE "99999[0-9]{10}" /data/db 2>/dev/null | head -5); if [ -n "$HITS" ]; then echo "  WARNING: plaintext IMSI found in:"; echo "$HITS"; else echo "  OK: no plaintext IMSI found in /data/db"; fi'

echo
echo "=== 8. webui (idempotent admin bootstrap + HTTP check) ==="
dc exec -T mongodb mongo --quiet /opt/init/init.js && echo "  init.js OK"
curl -s -o /dev/null -w '  webui http://localhost:9999/ -> HTTP %{http_code}\n' http://localhost:9999/ || true

echo
echo "=== Verification done ==="
