#!/bin/bash
# Mostra o estado da stack: containers, memória, índices do Elastic e últimos logs do coletor.
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a

echo "=== containers ==="
docker compose ps -a --format 'table {{.Service}}\t{{.Status}}'
echo
echo "=== memória ==="
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $(docker compose ps -q)
echo
echo "=== índices do Elasticsearch ==="
curl -sS -u "elastic:${ELASTIC_PASSWORD}" 'http://127.0.0.1:9200/_cat/indices/protheus-*?v&h=index,health,docs.count,store.size&s=index'
echo
echo "=== coletor (últimas linhas) ==="
docker compose logs coletor --no-log-prefix --tail "${1:-25}"
