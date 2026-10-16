#!/bin/sh
echo "=== /openbao/data ==="
ls -la /openbao/data/ 2>/dev/null || echo "empty or not found"
echo "=== env BAO_ADDR ==="
echo "$BAO_ADDR"
echo "=== Starting bao server ==="
bao server -config=/openbao/config/openbao.hcl &
sleep 3
echo "=== bao status ==="
export BAO_ADDR=http://127.0.0.1:8200
bao status -format=json 2>/dev/null || echo "status failed"
echo "=== bao operator init ==="
bao operator init -key-shares=1 -key-threshold=1 -format=json 2>&1 || echo "init failed"
