#!/bin/bash
set -e

echo "==================================================="
echo "PSMDB Encryption Verification"
echo "==================================================="
echo ""

echo "Step 1: Checking OpenBao status..."
if docker compose exec -T openbao wget -qO- http://127.0.0.1:8200/v1/sys/health | grep -q '"sealed":false'; then
  echo "  [OK] OpenBao is unsealed"
else
  echo "  [FAIL] OpenBao is sealed or unreachable"
fi

echo ""
echo "Step 2: Checking PSMDB encryption status..."
ENCRYPTION_STATUS=$(docker compose exec -T mongodb mongosh --quiet --eval "
  var p = db.adminCommand('getParameter', '*');
  if (p.security && p.security.enableEncryption) {
    print('ENCRYPTION_ENABLED');
  } else {
    print('ENCRYPTION_NOT_FOUND');
  }
" 2>/dev/null || docker compose exec -T mongodb mongo --quiet --eval "
  var p = db.adminCommand('getParameter', '*');
  if (p.security && p.security.enableEncryption) {
    print('ENCRYPTION_ENABLED');
  } else {
    print('ENCRYPTION_NOT_FOUND');
  }
" 2>/dev/null || echo "CHECK_FAILED")

if echo "$ENCRYPTION_STATUS" | grep -q "ENCRYPTION_ENABLED"; then
  echo "  [OK] PSMDB encryption is enabled"
elif echo "$ENCRYPTION_STATUS" | grep -q "ENCRYPTION_NOT_FOUND"; then
  echo "  [WARN] Encryption parameter not found in PSMDB config"
else
  echo "  [WARN] Could not verify encryption status via MongoDB shell"
fi

echo ""
echo "Step 3: Verifying data files are encrypted..."
DATA_DIR=".deploy/docker/local-data/mongodb"
if [ -d "$DATA_DIR" ]; then
  WT_FILE=$(find "$DATA_DIR" -name "*.wt" | head -1)
  if [ -n "$WT_FILE" ]; then
    echo "  [INFO] WiredTiger files present. Checking for plaintext..."
    PLAINTEXT=$(strings "$WT_FILE" 2>/dev/null | grep -iE "(admin|open5gs|accounts|security\.k|security\.opc)" | head -5 || true)
    if [ -n "$PLAINTEXT" ]; then
      echo "  [WARN] Possible plaintext found in data files:"
      echo "$PLAINTEXT" | sed 's/^/    /'
    else
      echo "  [OK] No obvious plaintext found in data files"
    fi
  else
    echo "  [INFO] No WiredTiger .wt files found in data directory"
  fi
else
  echo "  [WARN] Data directory not found at $DATA_DIR"
fi

echo ""
echo "Step 4: Testing Open5GS database connectivity..."
DB_CHECK=$(docker compose exec -T mongodb mongosh --quiet --eval "db.getMongo().getDBs()" 2>/dev/null | grep "open5gs" || \
           docker compose exec -T mongodb mongo --quiet --eval "db.getMongo().getDBs()" 2>/dev/null | grep "open5gs" || true)
if [ -n "$DB_CHECK" ]; then
  echo "  [OK] open5gs database is accessible"
else
  echo "  [FAIL] open5gs database not found or unreachable"
fi

echo ""
echo "Step 5: Checking Open5GS services health..."
for svc in webui hss udr; do
  if docker compose ps "$svc" | grep -q "healthy\|Up"; then
    echo "  [OK] $svc is running"
  else
    echo "  [INFO] $svc status unknown or not running"
  fi
done

echo ""
echo "==================================================="
echo "Verification complete"
echo "==================================================="
