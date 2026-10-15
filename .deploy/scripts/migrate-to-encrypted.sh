#!/bin/bash
set -e

echo "==================================================="
echo "Open5GS MongoDB -> PSMDB Encryption Migration Tool"
echo "==================================================="
echo ""

# Check if running in the project directory
if [ ! -f "docker-compose.yaml" ]; then
  echo "ERROR: docker-compose.yaml not found. Run this script from the project root."
  exit 1
fi

BACKUP_DIR=".deploy/docker/local-data/mongodb-backup-$(date +%Y%m%d-%H%M%S)"
echo "Step 1: Creating backup directory: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

echo ""
echo "Step 2: Stopping Open5GS services (keeping MongoDB running)..."
docker compose stop webui populate mme hss pcrf smf sgwc sgwu upf amf ausf udm udr pcf bsf nrf nssf scp || true

echo ""
echo "Step 3: Dumping existing MongoDB data..."
docker compose exec -T mongodb mongodump --out=/data/db/dump
docker compose cp mongodb:/data/db/dump "$BACKUP_DIR/"
docker compose exec -T mongodb rm -rf /data/db/dump

echo ""
echo "Step 4: Stopping MongoDB..."
docker compose stop mongodb
docker compose rm -f mongodb

echo ""
echo "Step 5: Backing up and removing old MongoDB data volume..."
OLD_DATA_DIR=".deploy/docker/local-data/mongodb-old-$(date +%Y%m%d-%H%M%S)"
if [ -d ".deploy/docker/local-data/mongodb" ]; then
  mv .deploy/docker/local-data/mongodb "$OLD_DATA_DIR"
fi

echo ""
echo "Step 6: Starting OpenBao and PSMDB with encryption..."
docker compose up -d openbao mongodb

echo ""
echo "Step 7: Waiting for PSMDB to be ready..."
sleep 10
until docker compose exec -T mongodb mongosh --eval "db.adminCommand('ping')" > /dev/null 2>&1 || \
      docker compose exec -T mongodb mongo --eval "db.adminCommand('ping')" > /dev/null 2>&1; do
  echo "Waiting for PSMDB..."
  sleep 2
done

echo ""
echo "Step 8: Restoring data to encrypted PSMDB..."
docker compose cp "$BACKUP_DIR/dump/." mongodb:/data/db/dump/
docker compose exec -T mongodb mongorestore /data/db/dump
docker compose exec -T mongodb rm -rf /data/db/dump

echo ""
echo "Step 9: Starting remaining Open5GS services..."
docker compose up -d

echo ""
echo "==================================================="
echo "Migration complete!"
echo "Backup saved to: $BACKUP_DIR"
echo "Old data saved to: $OLD_DATA_DIR"
echo "==================================================="
