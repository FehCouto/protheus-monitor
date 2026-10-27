#!/bin/bash
# Cria/recria os painéis do Zabbix e do Kibana.
cd "$(dirname "$0")/.." || exit 1
docker compose run --rm --no-deps -T \
  -v "$PWD/scripts/paineis.py:/app/paineis.py:ro" \
  -e KIBANA_URL=http://kibana:5601 \
  coletor python /app/paineis.py
