#!/bin/bash
ROOT_TOKEN=$(cat /home/mazay/open5gs-compose/.deploy/docker/local-data/openbao/secrets/root-token)
docker exec -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN=$ROOT_TOKEN open5gs-compose-openbao-1 sh -c 'bao secrets list'
