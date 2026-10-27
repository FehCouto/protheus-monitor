#!/bin/bash
# Mostra os endereços e as credenciais locais dos painéis (geradas no .env).
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a
cat <<FIM
Kibana  http://127.0.0.1:5601/app/dashboards#/view/ptm-visao-geral
        usuário: elastic   senha: ${ELASTIC_PASSWORD}

Zabbix  http://127.0.0.1:8080
        usuário: Admin     senha: ${ZABBIX_ADMIN_PASSWORD}
FIM
