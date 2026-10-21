#!/bin/bash
# DEPRECATED wrapper - kept for backward compatibility only.
#
# The stand has moved to OpenBao with TLS (:8200) + KV v2 and PSMDB 5.0
# (legacy `mongo` shell; no mongosh/openssl in the image), so the original
# checks of this script (plain HTTP health endpoint on :8200, mongosh)
# no longer apply.
#
# Use / see: .deploy/scripts/verify-encrypted-stack.sh
echo "NOTE: verify-encryption.sh is deprecated, redirecting to verify-encrypted-stack.sh" >&2
exec bash "$(dirname "$0")/verify-encrypted-stack.sh" "$@"

