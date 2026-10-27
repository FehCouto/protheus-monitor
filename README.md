# protheus-monitor

Monitoramento **local** do Protheus (produção, TOTVS Cloud) com Elastic + Zabbix, rodando em Docker
nesta máquina. Nada é exposto na rede: todas as portas ficam presas em `127.0.0.1`.

## Como funciona

```
SQL Server do Protheus (login somente leitura)
        │  consultas leves, uma por vez, NOLOCK
        ▼
   coletor (Python) ──► Elasticsearch ──► Kibana  (exploração, histórico, busca em log)
        │
        └────────────► Zabbix (itens trapper) ──► painel, gatilhos e alertas

   Zabbix ──► REST do Protheus e porta do SQL Server (checagem direta de disponibilidade)
```

O coletor é o único componente que fala com o banco. Cada consulta roda uma vez e alimenta as duas
ferramentas, então a carga em produção não dobra.

## Uso

Tudo roda de dentro do Debian (WSL), em `~/protheus-monitor`. O Docker Desktop precisa estar aberto.

| Ação | Comando |
|---|---|
| Subir | `docker compose up -d` |
| Parar (mantém os dados) | `docker compose stop` |
| Painéis não abrem depois de reabrir o Docker Desktop | `docker compose restart` |
| Estado, memória, índices e log | `scripts/estado.sh` |
| Endereços e senhas locais | `scripts/acessos.sh` |
| Recriar os painéis | `scripts/paineis.sh` |
| Captura de tela dos painéis | `docker compose run --rm foto` (grava em `capturas/`) |
| Apagar tudo, inclusive dados | `docker compose down -v` |

Painéis: Kibana em <http://127.0.0.1:5601> e Zabbix em <http://127.0.0.1:8080>.

## Consultas

Ficam em `coletor/consultas/*.yml`. Para incluir uma, acrescente um bloco e rode
`docker compose restart coletor` (o coletor cria os itens e gatilhos no Zabbix sozinho).

O coletor **recusa** consulta que não seja `SELECT` ou que não tenha `WITH (NOLOCK)`, impõe intervalo
mínimo de 60s e tempo máximo de 30s, e dobra o intervalo de uma consulta a cada falha (até 1h).
A sessão no banco usa `READ UNCOMMITTED`, `LOCK_TIMEOUT 5000` e `DEADLOCK_PRIORITY LOW`, e se
identifica como `protheus-monitor-local`.

| Tipo | O que faz |
|---|---|
| `metricas` | Uma linha; cada coluna vira item no Zabbix (`protheus.<id>.<coluna>`) e o conjunto vai para `protheus-metricas` |
| `eventos` | Leitura incremental por `R_E_C_N_O_`; cada linha vira documento no índice indicado |
| `amostras` | Várias linhas; cada execução grava uma foto no índice indicado |
| `rest` | GET no REST do Protheus (não passa pelo banco); as chaves de `raiz` viram itens no Zabbix e cada lista de `listas` vai para o índice indicado |

As consultas `rest` usam o usuário `PROTHEUS_REST_USER` / `PROTHEUS_REST_PASSWORD` do `.env` (token OAuth2,
uma tentativa por ciclo, 20s de limite). Sem o usuário elas ficam desligadas. Depois de preencher o
`.env`, rode `docker compose up -d coletor` (`restart` não relê o `.env`).

## Segredos e dados

- `.env` é gerado por `scripts/gera_env.php` a partir de `C:\QuerysBD\config\database.php` e contém a
  senha de produção. Não versionar, não compartilhar.
- As consultas do PLS só trazem contagens. Nenhum dado de beneficiário sai do banco.
- O log de processos (`protheus-proclog`) traz login de usuário interno e a mensagem da rotina.
- Retenção no Elasticsearch: 90 dias (`RETENCAO_DIAS` no `.env`).

## Limites conhecidos

- O login do banco não tem `VIEW SERVER STATE`: não dá para ver bloqueios, sessões nem consultas
  lentas do SQL Server.
- O que vive na memória do AppServer (threads, usuários conectados agora, licenças) não está em
  tabela; depende do endpoint `GET /rest/MYMONITOR/saude` (fonte WSRESTFUL em
  `C:\ADVPL\CUSTOM\endpoint\MYMONITOR.prw`), que precisa ser compilado no Protheus. Ele enxerga só o
  AppServer que atende o REST. Ver `plan/`.
- A máquina é local: o Protheus na nuvem não consegue enviar nada para cá. Toda integração é por
  leitura (SQL ou REST), nunca por envio a partir do AdvPL.
