#!/bin/bash
# Generate TLS certificates for OpenBao (server + CA) used by the PSMDB vault interface.
# Run ON THE SERVER from the project root or from anywhere (resolves its own path).
# Certificates are placed in .deploy/docker/openbao/tls/ and mounted into containers:
#   - openbao:  /openbao/tls (server.crt, server.key)
#   - mongodb:  /etc/mongodb-tls (ca.crt for serverCAFile)
set -euo pipefail
cd "$(dirname "$0")/../.."

TLS_DIR=".deploy/docker/openbao/tls"
mkdir -p "$TLS_DIR"
cd "$TLS_DIR"

if [ -s server.crt ] && [ -s server.key ] && [ -s ca.crt ]; then
  echo "TLS certificates already exist in $TLS_DIR, skipping generation."
  exit 0
fi

echo "Generating CA (10 years)..."
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout ca.key -out ca.crt \
  -subj "/CN=openbao-ca/O=pLTE-in-Box" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

echo "Generating server certificate (openbao, localhost, 127.0.0.1)..."
openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout server.key -out server.csr \
  -subj "/CN=openbao/O=pLTE-in-Box"

cat > server-ext.cnf <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:openbao,DNS:localhost,IP:127.0.0.1
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extfile server-ext.cnf

rm -f server.csr server-ext.cnf
chmod 600 ca.key server.key
chmod 644 ca.crt server.crt

echo "Verifying certificate..."
openssl verify -CAfile ca.crt server.crt

echo "TLS certificates generated in $TLS_DIR:"
ls -l ca.crt server.crt server.key
