#!/bin/sh
export BAO_ADDR=http://127.0.0.1:8200
vault_status() {
  OUTPUT=$(bao status -format=json 2>/dev/null)
  if echo "$OUTPUT" | grep -q '"initialized"'; then
    echo "$OUTPUT"
  else
    echo '{"initialized":false,"sealed":true}'
  fi
}
echo "=== vault_status output ==="
vault_status
echo "=== bao status raw ==="
bao status -format=json 2>&1
echo "=== exit code ==="
echo $?
