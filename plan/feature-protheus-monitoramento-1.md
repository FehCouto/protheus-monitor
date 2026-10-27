---
goal: Monitoramento local do Protheus (produção, TOTVS Cloud) com Elastic e Zabbix
version: 1.0
date_created: 2026-10-02
last_updated: 2026-10-07
owner: Felipe Couto
status: 'In progress'
tags: [feature, infrastructure, monitoring, protheus, sigapls]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

Stack de monitoramento que roda somente na máquina local (Docker no WSL Debian), lê dados reais do
Protheus de produção por SQL somente leitura e os apresenta no Kibana e no Zabbix. As fases 1 e 2
estão implementadas em `~/protheus-monitor`. As fases 3 a 5 são a continuação proposta.

## 1. Requirements & Constraints

- **REQ-001**: Exibir dados reais do Protheus de produção no Kibana (Elastic) e no Zabbix.
- **REQ-002**: Rodar somente na máquina local; nenhuma hospedagem externa.
- **REQ-003**: Cobrir operação técnica (Schedule, log de processos, disponibilidade) e filas do SIGAPLS.
- **SEC-001**: Portas publicadas apenas em `127.0.0.1` (5601 Kibana, 8080 Zabbix, 9200 Elasticsearch).
- **SEC-002**: Segredos apenas em `.env` (modo 600), gerado por `scripts/gera_env.php`; nunca em fonte.
- **SEC-003**: Nenhum dado identificável de beneficiário sai do banco; consultas do PLS trazem só contagens.
- **CON-001**: Protheus hospedado na TOTVS Cloud: sem acesso ao sistema operacional do AppServer, sem agente.
- **CON-002**: Login `ControleTI` é `db_datareader`, sem `VIEW SERVER STATE`: DMVs do SQL Server indisponíveis.
- **CON-003**: A máquina local não é alcançável pela nuvem: AdvPL não consegue enviar (push) dados para cá.
- **CON-004**: Banco é produção: só `SELECT` com `WITH (NOLOCK)`, uma consulta por vez, intervalo mínimo 60s, timeout 30s.
- **CON-005**: Docker dispõe de ~7,6 GB de RAM; Elasticsearch limitado a 2 GB e Kibana a 1,3 GB.
- **GUD-001**: Todo nome de tabela, campo ou domínio de status vem do dicionário (SX3) ou de fonte nativo, nunca de suposição.
- **PAT-001**: Um único coletor consulta o banco e alimenta as duas ferramentas (carga em produção não dobra).
- **PAT-002**: Consultas declaradas em YAML (`coletor/consultas/*.yml`); itens e gatilhos do Zabbix derivam do YAML.

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: Subir a infraestrutura local e o coletor com o primeiro conjunto de consultas.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Integrar o Docker Desktop ao WSL Debian (`IntegratedWslDistros`) | ✅ | 2026-10-02 |
| TASK-002 | `docker-compose.yml` com Elasticsearch 9.5.4, Kibana 9.5.4, Zabbix 7.0 LTS (server, web, PostgreSQL 16) | ✅ | 2026-10-02 |
| TASK-003 | `scripts/gera_env.php`: gera `.env` a partir de `C:\QuerysBD\config\database.php` sem exibir segredos | ✅ | 2026-10-02 |
| TASK-004 | `coletor/coletor.py`: executor de consultas com validação somente leitura, recuo exponencial e estado incremental | ✅ | 2026-10-02 |
| TASK-005 | `coletor/consultas/10-infra.yml`: Schedule (SCHDTSK), log de processos (CV8010), logons (SYS_USR_LOGIN), maiores tabelas | ✅ | 2026-10-02 |
| TASK-006 | `coletor/consultas/20-pls.yml`: guias (BEA010), fila da auditoria, PEGs (BCI010). Em 2026-10-07 saíram protocolos (B00010) e XML do portal (BXX010), e PEGs passou do ano para a competência do mês (`BCI_ANO` + `BCI_MES`) | ✅ | 2026-10-02 |
| TASK-007 | Checagens nativas do Zabbix: cenário web do REST e tempo de conexão das portas REST e SQL Server | ✅ | 2026-10-02 |

### Implementation Phase 2

- GOAL-002: Painéis e gatilhos.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | 11 gatilhos no Zabbix (REST fora, SQL inacessível, coletor parado, Schedule parado, erro de processo, etc.). Eram 12; o de XML preso saiu com a consulta em 2026-10-07 | ✅ | 2026-10-02 |
| TASK-009 | `scripts/paineis.py`: dashboard "Protheus - Visão geral" no Zabbix | ✅ | 2026-10-02 |
| TASK-010 | `scripts/paineis.py`: data views e dashboard "Protheus - Visão geral" no Kibana | ✅ | 2026-10-02 |
| TASK-011 | `scripts/foto/`: captura de tela headless dos painéis para conferência visual | ✅ | 2026-10-02 |
| TASK-012 | Ajustar limiares dos gatilhos depois de 1 semana de histórico real | | |

### Implementation Phase 3

- GOAL-003: Métricas que só existem na memória do AppServer, via endpoint REST customizado (exige compilação no Protheus).

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-013 | Endpoint `GET /rest/MYMONITOR/saude` somente leitura: threads e usuários conectados (`GetUserInfoArray`), por ambiente e rotina. Fonte WSRESTFUL escrito em 2026-10-05 e validado na pré-compilação local; ainda não compilado no servidor | | |
| TASK-014 | Usuário Protheus dedicado ao monitoramento, com acesso apenas a esse endpoint | | |
| TASK-015 | Novo tipo de consulta `rest` no coletor: token OAuth2 (password grant) em cache por host, timeout 20s, sem reenvio | ✅ | 2026-10-05 |
| TASK-016 | Validar em homologação (`SEU-AMBIENTE-HOMOLOG`) antes de produção | | |

### Implementation Phase 4

- GOAL-004: Dados que o AdvPL grava e o coletor lê (substitui o push, impossível para máquina local).

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | Tabela customizada de batimento de rotinas (rotina, início, fim, situação, mensagem) gravada pelos jobs críticos | | |
| TASK-018 | Consulta `eventos` no coletor lendo essa tabela; gatilho "rotina X não rodou no horário" | | |

### Implementation Phase 5

- GOAL-005: Ampliar cobertura e avisos.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-019 | Avisos do Zabbix por Teams, Telegram ou e-mail (saída da máquina local funciona) | | |
| TASK-020 | Segundo host no Zabbix para homologação (`SEU_BANCO_HOMOLOG`) | | |
| TASK-021 | Financeiro: boletos gerados (CV8 `FINX999`), títulos em aberto por vencimento | | |
| TASK-022 | Regulatório: protocolos/NIP próximos do prazo da ANS (B00/B4Q/B5J) | | |
| TASK-023 | Trilha de auditoria (`*_TTAT_LOG`): volume de alterações por tabela e usuário | | |

## 3. Alternatives

- **ALT-001**: Logstash com JDBC para o Elastic e ODBC no Zabbix. Rejeitado: cada ferramenta consultaria produção por conta própria e o Logstash custa ~1 GB de RAM.
- **ALT-002**: Plugin MSSQL do Zabbix Agent 2 com consultas customizadas. Rejeitado pelo mesmo motivo de carga duplicada.
- **ALT-003**: AdvPL enviando métricas por HTTP (push). Inviável: a máquina local não é alcançável pela TOTVS Cloud.
- **ALT-004**: Grafana no lugar de Kibana/Zabbix. Não adotado: a escolha foi Elastic e Zabbix.
- **ALT-005**: Endpoint em TLPP anotado (`@Get`). Abandonado em 2026-10-05: `tlpp-core.th` e `tlpp-rest.th` não existem em `C:\ADVPL\INCLUDES` e a compilação para em `C2090 File not found tlpp-core.th`. O endpoint virou WSRESTFUL, como os demais customizados.

## 4. Dependencies

- **DEP-001**: Docker Desktop em execução, com integração WSL para a distro Debian.
- **DEP-002**: Rede até `SEU.IP.DO.SQLSERVER:37000` (SQL Server) e `SEU-AMBIENTE.protheus.cloudtotvs.com.br:4050` (REST).
- **DEP-003**: `C:\QuerysBD\config\database.php` como origem das credenciais do banco.
- **DEP-004**: Fase 3 depende de compilação e publicação de fonte AdvPL no ambiente da TOTVS Cloud.

## 5. Files

- **FILE-001**: `docker-compose.yml` - serviços, limites de memória, portas em loopback.
- **FILE-002**: `coletor/coletor.py` - executor, cliente Elasticsearch, sender e API do Zabbix.
- **FILE-003**: `coletor/consultas/10-infra.yml`, `coletor/consultas/20-pls.yml` - catálogo de consultas, métricas e gatilhos.
- **FILE-004**: `scripts/paineis.py` - painéis do Zabbix e do Kibana.
- **FILE-005**: `scripts/gera_env.php`, `scripts/estado.sh`, `scripts/valores.sh`, `scripts/acessos.sh`, `scripts/foto/`.
- **FILE-006**: `C:\ADVPL\CUSTOM\endpoint\MYMONITOR.prw` - endpoint `GET /rest/MYMONITOR/saude` (fica no workspace de compilação do Protheus, fora deste projeto).
- **FILE-007**: `coletor/consultas/30-appserver.yml` - consulta `rest` que lê o endpoint; desligada enquanto `PROTHEUS_REST_USER` estiver vazio.

## 6. Testing

- **TEST-001**: `scripts/estado.sh` mostra todos os containers ativos e as 9 consultas com `ok`.
- **TEST-002**: `scripts/valores.sh` mostra todos os itens com valor recente e sem erro.
- **TEST-003**: `docker compose run --rm foto` gera capturas dos dois painéis com dados.
- **TEST-004**: Consulta nova com `UPDATE` ou sem `NOLOCK` em `coletor/consultas/` faz o coletor recusar a carga.

## 7. Risks & Assumptions

- **RISK-001**: Consulta de monitoramento pesar em produção. Mitigado por CON-004, recuo exponencial e ciclo total de ~2,5s a cada 5 min.
- **RISK-002**: Memória da máquina (15,7 GB) apertada com a stack (~3,2 GB) somada ao uso normal.
- **RISK-003**: Significado de `TSK_STATUS` 3 e 5 em `SCHDTSK` foi inferido dos dados (sem CBOX no dicionário).
- **RISK-004**: A coleta só acontece com a máquina ligada e o Docker Desktop aberto: de 02/10 a 05/10 houve poucas horas de histórico, o que adia a TASK-012.
- **RISK-005**: `GetUserInfoArray` enxerga só o AppServer em que a requisição cai. Se na TOTVS Cloud o REST rodar em servidor separado do SmartClient, o endpoint mostra as threads do REST e não os usuários de tela. Só a TASK-016 responde isso.
- **RISK-006**: Senha errada em `PROTHEUS_REST_PASSWORD` gera uma tentativa de login a cada ciclo (com recuo até 1h) e pode bloquear o usuário de monitoramento no Protheus.
- **ASSUMPTION-001**: O relógio do SQL Server está no mesmo fuso da máquina local (America/Sao_Paulo).
- **ASSUMPTION-002**: O login `ControleTI` continuará somente leitura.

## 8. Related Specifications / Further Reading

- `README.md` deste projeto.
- Fonte nativo `adm/CTBXLOG.PRW` (`ProcLogAtu`, domínio de `CV8_INFO`).
- Fonte nativo `plano de saude/PLSXFUN.PRW` (`PLSSTATUS`, domínio de `BEA_STATUS`).
- Skill `protheus-query-scripts` (regras obrigatórias de consulta em produção).
