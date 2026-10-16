#!/bin/bash
ROOT_TOKEN=$(cat /home/mazay/open5gs-compose/.deploy/docker/local-data/openbao/secrets/root-token)
KEY=$(head -c 32 /dev/urandom | base64)
curl -s -H "X-Vault-Token: $ROOT_TOKEN" -X POST http://127.0.0.1:8200/v1/secret/data/psmdb-encryption -d "{\"data\":{\"value\":\"$KEY\"}}"
