#!/bin/bash
# Último valor de cada item do Zabbix e problemas em aberto, direto no terminal.
cd "$(dirname "$0")/.." || exit 1
docker compose run --rm --no-deps -T \
  -v "$PWD/scripts/valores.py:/app/valores.py:ro" \
  coletor python /app/valores.py
