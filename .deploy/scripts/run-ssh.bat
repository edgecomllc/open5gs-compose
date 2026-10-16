@echo off
ssh -b 192.168.0.2 mazay@192.168.0.9 "docker exec open5gs-compose-openbao-1 sh -c 'BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=`cat /tmp/root-token` bao secrets disable secret/'"
ssh -b 192.168.0.2 mazay@192.168.0.9 "docker exec open5gs-compose-openbao-1 sh -c 'BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=`cat /tmp/root-token` bao secrets enable -path=secret kv-v2'"
ssh -b 192.168.0.2 mazay@192.168.0.9 "docker exec open5gs-compose-openbao-1 sh -c 'BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN=`cat /tmp/root-token` bao kv put -mount=secret psmdb-encryption value=`head -c 32 /dev/urandom | base64`'"
