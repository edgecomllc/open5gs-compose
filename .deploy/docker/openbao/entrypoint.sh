#!/bin/sh
set -e

# Start OpenBao server in background
bao server -config=/openbao/config/openbao.hcl &
BAO_PID=$!

# Wait for OpenBao API to be ready
echo "Waiting for OpenBao to start..."
until wget -qO- http://127.0.0.1:8200/v1/sys/health > /dev/null 2>&1; do
  sleep 1
done
echo "OpenBao API is ready."

# Check initialization status
HEALTH=$(wget -qO- http://127.0.0.1:8200/v1/sys/health 2>/dev/null || echo '{"initialized":false,"sealed":true}')

if echo "$HEALTH" | grep -q '"initialized":false'; then
  echo "OpenBao not initialized. Initializing now..."

  INIT_RESPONSE=$(wget -qO- --post-data='{"secret_shares":1,"secret_threshold":1}' http://127.0.0.1:8200/v1/sys/init)

  ROOT_TOKEN=$(echo "$INIT_RESPONSE" | grep -o '"root_token":"[^"]*"' | cut -d'"' -f4)
  UNSEAL_KEY=$(echo "$INIT_RESPONSE" | grep -o '"keys":\["[^"]*"\]' | cut -d'"' -f3)

  if [ -z "$ROOT_TOKEN" ] || [ -z "$UNSEAL_KEY" ]; then
    echo "ERROR: Failed to extract root token or unseal key from initialization response."
    kill $BAO_PID
    exit 1
  fi

  mkdir -p /openbao/secrets
  echo "$ROOT_TOKEN" > /openbao/secrets/root-token
  echo "$UNSEAL_KEY" > /openbao/secrets/unseal-key
  chmod 600 /openbao/secrets/root-token /openbao/secrets/unseal-key

  echo "Unsealing OpenBao..."
  wget -qO- --post-data="{\"key\":\"$UNSEAL_KEY\"}" http://127.0.0.1:8200/v1/sys/unseal > /dev/null

  echo "Configuring secrets engine and encryption key..."

  # Enable KV v1 secrets engine at path 'secret'
  wget -qO- --header="X-Vault-Token: $ROOT_TOKEN" --post-data='{"type":"kv","options":{"version":"1"}}' http://127.0.0.1:8200/v1/sys/mounts/secret > /dev/null 2>&1 || true

  # Generate a 32-byte base64 encryption key for PSMDB
  ENCRYPTION_KEY=$(openssl rand -base64 32)

  # Store the encryption key
  wget -qO- --header="X-Vault-Token: $ROOT_TOKEN" --post-data="{\"value\":\"$ENCRYPTION_KEY\"}" http://127.0.0.1:8200/v1/secret/psmdb-encryption > /dev/null 2>&1 || true

  # Save token for PSMDB access (readable by mongod user UID 1001)
  echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
  chmod 644 /openbao/secrets/vault-token

  echo "OpenBao initialized. Root token and unseal key saved to /openbao/secrets/"

elif echo "$HEALTH" | grep -q '"sealed":true'; then
  echo "OpenBao is sealed. Unsealing..."

  if [ ! -f /openbao/secrets/unseal-key ]; then
    echo "ERROR: Unseal key not found at /openbao/secrets/unseal-key"
    echo "OpenBao was initialized but unseal key is missing. Manual recovery required."
    kill $BAO_PID
    exit 1
  fi

  UNSEAL_KEY=$(cat /openbao/secrets/unseal-key)
  wget -qO- --post-data="{\"key\":\"$UNSEAL_KEY\"}" http://127.0.0.1:8200/v1/sys/unseal > /dev/null

  ROOT_TOKEN=$(cat /openbao/secrets/root-token)
  echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
  chmod 644 /openbao/secrets/vault-token

  echo "OpenBao unsealed."
else
  echo "OpenBao is already initialized and unsealed."
  if [ -f /openbao/secrets/root-token ]; then
    ROOT_TOKEN=$(cat /openbao/secrets/root-token)
    echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
    chmod 644 /openbao/secrets/vault-token
  fi
fi

# Verify unsealed status
HEALTH=$(wget -qO- http://127.0.0.1:8200/v1/sys/health 2>/dev/null || echo '{"sealed":true}')
if echo "$HEALTH" | grep -q '"sealed":true'; then
  echo "ERROR: OpenBao is still sealed after unseal attempt."
  kill $BAO_PID
  exit 1
fi

echo "OpenBao is ready and unsealed."

# Keep OpenBao running in foreground and handle signals
trap 'kill -TERM $BAO_PID' TERM INT
wait $BAO_PID
trap - TERM INT
wait $BAO_PID
EXIT_STATUS=$?

echo "OpenBao server exited with status $EXIT_STATUS"
exit $EXIT_STATUS
