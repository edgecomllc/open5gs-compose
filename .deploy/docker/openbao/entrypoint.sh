#!/bin/sh

# Helper: fetch Vault status JSON via local CLI.
# bao status returns exit code 1 for sealed/uninitialized vaults, but still prints valid JSON.
vault_status() {
  OUTPUT=$(bao status -format=json 2>/dev/null) || true
  if echo "$OUTPUT" | grep -q '"initialized"'; then
    echo "$OUTPUT"
  else
    echo '{"initialized":false,"sealed":true}'
  fi
}

# Start OpenBao server in background
bao server -config=/openbao/config/openbao.hcl &
BAO_PID=$!

# Wait for OpenBao API to be ready (any HTTP response means it's up)
echo "Waiting for OpenBao to start..."
until wget --server-response -qO- http://127.0.0.1:8200/v1/sys/health 2>&1 | grep -q "HTTP/1.1"; do
  sleep 1
done
echo "OpenBao API is ready."

export BAO_ADDR=http://127.0.0.1:8200

# Check initialization status
HEALTH=$(vault_status)
echo "DEBUG vault_status: $HEALTH"

if echo "$HEALTH" | grep -q '"initialized": false'; then
  echo "OpenBao not initialized. Initializing now..."

  INIT_OUTPUT=$(bao operator init -key-shares=1 -key-threshold=1 -format=json 2>&1) || true
  echo "DEBUG init output: $INIT_OUTPUT"

  # Check if init succeeded
  if echo "$INIT_OUTPUT" | grep -q '"root_token"'; then
    ROOT_TOKEN=$(echo "$INIT_OUTPUT" | awk -F'"' '/root_token/ {print $4}')
    UNSEAL_KEY=$(echo "$INIT_OUTPUT" | awk -F'"' '/unseal_keys_b64/ {getline; print $2}')

    if [ -z "$ROOT_TOKEN" ] || [ -z "$UNSEAL_KEY" ]; then
      echo "ERROR: Failed to extract root token or unseal key from initialization output."
      echo "INIT_OUTPUT was: $INIT_OUTPUT"
      kill $BAO_PID
      exit 1
    fi

    mkdir -p /openbao/secrets
    echo "$ROOT_TOKEN" > /openbao/secrets/root-token
    echo "$UNSEAL_KEY" > /openbao/secrets/unseal-key
    chmod 600 /openbao/secrets/root-token /openbao/secrets/unseal-key

    echo "Unsealing OpenBao..."
    bao operator unseal "$UNSEAL_KEY" > /dev/null

    echo "Configuring secrets engine and encryption key..."

    # Enable KV v1 secrets engine at path 'secret'
    bao secrets enable -path=secret -version=1 kv > /dev/null 2>&1 || true

    # Generate a 32-byte base64 encryption key for PSMDB
    ENCRYPTION_KEY=$(head -c 32 /dev/urandom | base64)

    # Store the encryption key
    bao write secret/psmdb-encryption value="$ENCRYPTION_KEY" > /dev/null 2>&1 || true

    # Save token for PSMDB access (readable only by mongod user UID 1001)
    echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
    chmod 600 /openbao/secrets/vault-token
    chown 1001:1001 /openbao/secrets/vault-token

    echo "OpenBao initialized. Root token and unseal key saved to /openbao/secrets/"
  else
    echo "ERROR: OpenBao initialization failed."
    echo "INIT_OUTPUT was: $INIT_OUTPUT"
    kill $BAO_PID
    exit 1
  fi

elif echo "$HEALTH" | grep -q '"sealed": true'; then
  echo "OpenBao is sealed. Unsealing..."

  if [ ! -f /openbao/secrets/unseal-key ]; then
    echo "ERROR: Unseal key not found at /openbao/secrets/unseal-key"
    echo "OpenBao was initialized but unseal key is missing. Manual recovery required."
    kill $BAO_PID
    exit 1
  fi

  UNSEAL_KEY=$(cat /openbao/secrets/unseal-key)
  bao operator unseal "$UNSEAL_KEY" > /dev/null

  ROOT_TOKEN=$(cat /openbao/secrets/root-token)
  echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
  chmod 600 /openbao/secrets/vault-token
  chown 1001:1001 /openbao/secrets/vault-token

  echo "OpenBao unsealed."
else
  echo "OpenBao is already initialized and unsealed."
  if [ -f /openbao/secrets/root-token ]; then
    ROOT_TOKEN=$(cat /openbao/secrets/root-token)
    echo "$ROOT_TOKEN" > /openbao/secrets/vault-token
    chmod 600 /openbao/secrets/vault-token
    chown 1001:1001 /openbao/secrets/vault-token
  fi
fi

# Verify unsealed status
HEALTH=$(vault_status)
if echo "$HEALTH" | grep -q '"sealed": true'; then
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
