#!/bin/bash
# DEPRECATED wrapper - kept for backward compatibility only.
#
# The stand has moved to OpenBao with TLS (:8200) + KV v2; migration is now
# handled by the idempotent 8-step script (TLS certs, mongodump, backups,
# chown 1001:1001, up openbao+mongodb, mongorestore, full stack, verification).
#
# Use / see: .deploy/scripts/deploy-encrypted.sh
echo "NOTE: migrate-to-encrypted.sh is deprecated, redirecting to deploy-encrypted.sh" >&2
exec bash "$(dirname "$0")/deploy-encrypted.sh" "$@"

