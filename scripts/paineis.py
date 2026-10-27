#!/usr/bin/env python3
"""Cria (ou recria) os painéis: dashboard no Zabbix e data views + dashboard no Kibana.

Idempotente. Rodar com scripts/paineis.sh depois que o coletor já tiver enviado dados.
"""
import json
import os
import sys

import requests

sys.path.insert(0, "/app")
import coletor  # noqa: E402  (reaproveita o cliente da API do Zabbix e as consultas)

KIBANA = os.environ.get("KIBANA_URL", "http://kibana:5601")
AUTH = ("elastic", os.environ["ELASTIC_PASSWORD"])
CAB = {"kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana"}


# --------------------------------------------------------------------------- Zabbix
def painel_zabbix():
    z = coletor.Zabbix()
    z.login()
    hid = z.chama("host.get", {"filter": {"host": coletor.ZBX_HOST}, "output": ["hostid"]})[0]["hostid"]
    itens = {i["key_"]: i["itemid"] for i in z.chama(
        "item.get", {"hostids": hid, "output": ["itemid", "key_"], "webitems": True})}
    cenario = coletor.CENARIO_REST
    refs = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def valor(chave, rotulo, x, y, largura=12, altura=3):
        # Mostra só o valor (2) e a seta de variação (4); o rótulo vai no título do widget.
        return {"type": "item", "name": rotulo, "x": x, "y": y, "width": largura, "height": altura,
                "fields": [{"type": 4, "name": "itemid.0", "value": itens[chave]},
                           {"type": 0, "name": "show.0", "value": 2}, {"type": 0, "name": "show.1", "value": 4},
                           {"type": 0, "name": "decimal_places", "value": 0}]}

    def grafico(titulo, series, x, y, largura=36, altura=6):
        campos = [{"type": 1, "name": "reference", "value": next(refs) * 5}]
        for n, (nome_item, cor) in enumerate(series):
            campos += [
                {"type": 1, "name": f"ds.{n}.hosts.0", "value": coletor.ZBX_NOME},
                {"type": 1, "name": f"ds.{n}.items.0", "value": nome_item},
                {"type": 1, "name": f"ds.{n}.color", "value": cor},
            ]
        return {"type": "svggraph", "name": titulo, "x": x, "y": y, "width": largura, "height": altura,
                "fields": campos}

    web_tempo = f"web.test.time[{cenario},raiz,resp]"
    widgets = [
        # Linha 1: saúde técnica
        valor(web_tempo, "REST: tempo de resposta", 0, 0),
        valor("protheus.sql.latencia_ms", "SQL Server: tempo de resposta", 12, 0),
        valor("protheus.schedule.min_sem_execucao", "Schedule: tempo sem finalizar tarefa", 24, 0),
        valor("protheus.schedule.finalizadas", "Schedule: tarefas finalizadas hoje", 36, 0),
        valor("protheus.proclog.erros", "Processos: erros hoje", 48, 0),
        valor("protheus.usuarios.logaram_hoje", "Usuários que entraram hoje", 60, 0),
        # Linha 2: filas do PLS
        valor("protheus.pls_guias.total", "Guias digitadas hoje", 0, 3),
        valor("protheus.pls_auditoria.fila", "Guias na fila da auditoria", 12, 3),
        valor("protheus.pls_pegs.em_digitacao", "PEGs do mês em digitação", 24, 3),
        valor("protheus.pls_pegs.em_conferencia", "PEGs do mês em conferência", 36, 3),
        valor("protheus.pls_pegs.prontas", "PEGs do mês prontas", 48, 3),
        valor("protheus.pls_pegs.faturadas", "PEGs do mês faturadas", 60, 3),
        # Linha 3: problemas + disponibilidade
        {"type": "problems", "name": "Problemas em aberto", "x": 0, "y": 6, "width": 36, "height": 6,
         "fields": [{"type": 3, "name": "hostids.0", "value": hid}]},
        grafico("Tempo de resposta: REST e SQL Server", [
            ('Response time for step "raiz" of scenario "REST Protheus".', "1E88E5"),
            ("Porta do SQL Server: tempo de conexão", "FB8C00"),
        ], 36, 6),
        # Linha 4: negócio
        grafico("Guias de hoje por situação", [
            ("PLS guias: autorizadas hoje", "43A047"),
            ("PLS guias: não autorizadas hoje", "E53935"),
            ("PLS guias: de hoje ainda em auditoria", "FB8C00"),
        ], 0, 12),
        grafico("Schedule e processos: ocorrências na última hora", [
            ("Schedule: tarefas em execução", "1E88E5"),
            ("Schedule: tarefas descartadas na última hora", "E53935"),
            ("Processos: erros na última hora", "8E24AA"),
        ], 36, 12),
        grafico("Filas do PLS", [
            ("PLS auditoria: guias em análise (60 dias)", "FB8C00"),
            ("PLS PEGs: em digitação (mês)", "1E88E5"),
            ("PLS PEGs: em conferência (mês)", "8E24AA"),
        ], 0, 18),
        grafico("Usuários", [
            ("Usuários: entraram hoje", "43A047"),
            ("Usuários: último logon na última hora", "1E88E5"),
        ], 36, 18),
    ]
    painel = {"name": "Protheus - Visão geral", "display_period": 60, "auto_start": 1,
              "pages": [{"name": "Visão geral", "widgets": widgets}]}
    existe = z.chama("dashboard.get", {"filter": {"name": painel["name"]}, "output": ["dashboardid"]})
    if existe:
        did = existe[0]["dashboardid"]
        z.chama("dashboard.update", {"dashboardid": did, **painel})
    else:
        did = z.chama("dashboard.create", painel)["dashboardids"][0]
    try:
        admin = z.chama("user.get", {"filter": {"username": "Admin"}, "output": ["userid"]})[0]["userid"]
        z.chama("user.update", {"userid": admin, "lang": "pt_BR"})
    except RuntimeError as e:
        print(f"Zabbix: interface segue em inglês ({e})")
    print(f"Zabbix: dashboard pronto -> http://127.0.0.1:8080/zabbix.php?action=dashboard.view&dashboardid={did}")


# --------------------------------------------------------------------------- Kibana
def kb(metodo, caminho, **kw):
    r = requests.request(metodo, f"{KIBANA}{caminho}", auth=AUTH, headers=CAB, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Kibana {metodo} {caminho}: {r.status_code} {r.text[:400]}")
    return r.json()


DATA_VIEWS = {
    "protheus-metricas": "Protheus - métricas",
    "protheus-schedule": "Protheus - execuções do Schedule",
    "protheus-proclog": "Protheus - log de processos",
    "protheus-tabelas": "Protheus - maiores tabelas",
}


def col_data():
    return {"dataType": "date", "isBucketed": True, "label": "@timestamp", "operationType": "date_histogram",
            "params": {"interval": "auto", "includeEmptyRows": True}, "scale": "interval",
            "sourceField": "@timestamp"}


def col_contagem(rotulo):
    return {"dataType": "number", "isBucketed": False, "label": rotulo, "customLabel": True,
            "operationType": "count", "scale": "ratio", "sourceField": "___records___",
            "params": {"emptyAsNull": True}}


def col_num(operacao, campo, rotulo):
    c = {"dataType": "number", "isBucketed": False, "label": rotulo, "customLabel": True,
         "operationType": operacao, "scale": "ratio", "sourceField": campo}
    if operacao == "last_value":
        c["params"] = {"sortField": "@timestamp"}
        c["filter"] = {"query": f'"{campo}": *', "language": "kuery"}
    return c


def col_termos(campo, rotulo, ordenar_por, tamanho=10):
    return {"dataType": "string", "isBucketed": True, "label": rotulo, "customLabel": True,
            "operationType": "terms", "scale": "ordinal", "sourceField": campo,
            "params": {"size": tamanho, "orderBy": {"type": "column", "columnId": ordenar_por},
                       "orderDirection": "desc", "otherBucket": False, "missingBucket": False,
                       "parentFormat": {"id": "terms"}}}


def lens(ident, titulo, dv, tipo, visualizacao, colunas, consulta=""):
    return {
        "type": "lens", "id": ident, "typeMigrationVersion": "8.9.0", "coreMigrationVersion": "8.8.0",
        "attributes": {
            "title": titulo, "description": "", "visualizationType": tipo,
            "state": {
                "visualization": visualizacao,
                "query": {"query": consulta, "language": "kuery"},
                "filters": [],
                "datasourceStates": {"formBased": {"layers": {"l1": {
                    "columnOrder": list(colunas), "columns": colunas, "incompleteColumns": {}}}}},
                "internalReferences": [], "adHocDataViews": {},
            },
        },
        "references": [{"type": "index-pattern", "id": dv, "name": "indexpattern-datasource-layer-l1"}],
    }


def numero(ident, titulo, campo):
    return lens(ident, titulo, "protheus-metricas", "lnsMetric",
                {"layerId": "l1", "layerType": "data", "metricAccessor": "m"},
                {"m": col_num("last_value", campo, titulo)})


def linhas(ident, titulo, series):
    colunas = {"x": col_data()}
    for n, (campo, rotulo) in enumerate(series):
        colunas[f"y{n}"] = col_num("max", campo, rotulo)
    return lens(ident, titulo, "protheus-metricas", "lnsXY", xy("line", [f"y{n}" for n in range(len(series))]),
                colunas)


def xy(tipo, metricas, quebra=None, x="x"):
    camada = {"layerId": "l1", "layerType": "data", "seriesType": tipo, "xAccessor": x, "accessors": metricas}
    if quebra:
        camada["splitAccessor"] = quebra
    return {"legend": {"isVisible": True, "position": "right"}, "valueLabels": "hide",
            "fittingFunction": "Linear", "preferredSeriesType": tipo, "layers": [camada],
            "axisTitlesVisibilitySettings": {"x": False, "yLeft": False, "yRight": False}}


def objetos_kibana():
    objs = [
        numero("ptm-n-guias", "Guias digitadas hoje", "pls_guias.total"),
        numero("ptm-n-auditoria", "Guias na fila da auditoria", "pls_auditoria.fila"),
        numero("ptm-n-pegs", "PEGs do mês em digitação", "pls_pegs.em_digitacao"),
        numero("ptm-n-usuarios", "Usuários que entraram hoje", "usuarios.logaram_hoje"),
        numero("ptm-n-schedule", "Tarefas do Schedule finalizadas hoje", "schedule.finalizadas"),
        numero("ptm-n-erros", "Erros de processamento hoje", "proclog.erros"),
        linhas("ptm-l-guias", "Guias de hoje por situação", [
            ("pls_guias.autorizadas", "Autorizadas"), ("pls_guias.negadas", "Não autorizadas"),
            ("pls_guias.em_auditoria", "Em auditoria"), ("pls_guias.canceladas", "Canceladas")]),
        linhas("ptm-l-filas", "Filas do PLS", [
            ("pls_auditoria.fila", "Auditoria (60 dias)"), ("pls_pegs.em_digitacao", "PEGs do mês em digitação"),
            ("pls_pegs.em_conferencia", "PEGs do mês em conferência")]),
        lens("ptm-b-schedule", "Execuções do Schedule por situação", "protheus-schedule", "lnsXY",
             xy("bar_stacked", ["c"], "s"),
             {"x": col_data(), "s": col_termos("status_desc", "Situação", "c", 6), "c": col_contagem("Execuções")}),
        lens("ptm-t-rotinas", "Rotinas mais executadas pelo Schedule", "protheus-schedule", "lnsDatatable",
             {"layerId": "l1", "layerType": "data",
              "columns": [{"columnId": "r"}, {"columnId": "s"}, {"columnId": "c"}]},
             {"r": col_termos("rotina", "Rotina", "c", 15), "s": col_termos("status_desc", "Situação", "c", 5),
              "c": col_contagem("Execuções")}),
        lens("ptm-b-proclog", "Log de processos por tipo de ocorrência", "protheus-proclog", "lnsXY",
             xy("bar_stacked", ["c"], "s"),
             {"x": col_data(), "s": col_termos("tipo_desc", "Tipo", "c", 6), "c": col_contagem("Ocorrências")}),
        # Só execuções e ocorrências (sem as mensagens informativas, que são a maioria do volume).
        lens("ptm-t-processos", "Rotinas processadas: execuções, alertas e erros", "protheus-proclog",
             "lnsDatatable",
             {"layerId": "l1", "layerType": "data",
              "columns": [{"columnId": "p"}, {"columnId": "s"}, {"columnId": "c"}]},
             {"p": col_termos("processo", "Processo", "c", 15), "s": col_termos("tipo_desc", "Tipo", "c", 6),
              "c": col_contagem("Ocorrências")},
             consulta='tipo: ("1" or "3" or "4" or "5")'),
        lens("ptm-b-tabelas", "Maiores tabelas do banco (linhas)", "protheus-tabelas", "lnsXY",
             xy("bar_horizontal", ["m"], x="t"),
             {"t": col_termos("tabela", "Tabela", "m", 15), "m": col_num("max", "linhas", "Linhas")}),
        {
            "type": "search", "id": "ptm-s-erros", "typeMigrationVersion": "8.0.0", "coreMigrationVersion": "8.8.0",
            "attributes": {
                "title": "Alertas, erros e cancelamentos de processos", "description": "",
                "columns": ["processo", "tipo_desc", "usuario", "mensagem", "detalhe"],
                "sort": [["@timestamp", "desc"]],
                "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({
                    "query": {"query": 'tipo: ("3" or "4" or "5")', "language": "kuery"}, "filter": [],
                    "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"})},
            },
            "references": [{"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern",
                            "id": "protheus-proclog"}],
        },
    ]

    # (id do objeto, tipo, x, y, largura, altura) na grade de 48 colunas do Kibana
    disposicao = [
        ("ptm-n-guias", "lens", 0, 0, 8, 6), ("ptm-n-auditoria", "lens", 8, 0, 8, 6),
        ("ptm-n-pegs", "lens", 16, 0, 8, 6), ("ptm-n-usuarios", "lens", 24, 0, 8, 6),
        ("ptm-n-schedule", "lens", 32, 0, 8, 6), ("ptm-n-erros", "lens", 40, 0, 8, 6),
        ("ptm-l-guias", "lens", 0, 6, 24, 13), ("ptm-l-filas", "lens", 24, 6, 24, 13),
        ("ptm-b-schedule", "lens", 0, 19, 24, 13), ("ptm-t-rotinas", "lens", 24, 19, 24, 13),
        ("ptm-b-proclog", "lens", 0, 32, 24, 13), ("ptm-t-processos", "lens", 24, 32, 24, 13),
        ("ptm-s-erros", "search", 0, 45, 32, 16), ("ptm-b-tabelas", "lens", 32, 45, 16, 16),
    ]
    paineis, refs = [], []
    for n, (ident, tipo, x, y, w, h) in enumerate(disposicao, 1):
        i = f"p{n}"
        # Nos blocos de número o rótulo já aparece dentro do bloco; o título do painel seria repetição.
        config = {"hidePanelTitles": True} if ident.startswith("ptm-n-") else {}
        paineis.append({"version": "8.9.0", "type": tipo, "panelIndex": i, "panelRefName": f"panel_{i}",
                        "gridData": {"x": x, "y": y, "w": w, "h": h, "i": i}, "embeddableConfig": config})
        refs.append({"name": f"{i}:panel_{i}", "type": tipo, "id": ident})
    objs.append({
        "type": "dashboard", "id": "ptm-visao-geral", "typeMigrationVersion": "8.9.0",
        "coreMigrationVersion": "8.8.0",
        "attributes": {
            "title": "Protheus - Visão geral", "description": "Monitoramento local do Protheus (coletor SQL).",
            "panelsJSON": json.dumps(paineis),
            "optionsJSON": json.dumps({"useMargins": True, "hidePanelTitles": False}),
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 60000},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})},
        },
        "references": refs,
    })
    return objs


def painel_kibana():
    try:
        kb("POST", "/internal/kibana/settings", json={"changes": {
            "format:number:defaultLocale": "pt-br", "dateFormat": "DD/MM/YYYY HH:mm:ss",
            "defaultRoute": "/app/dashboards#/view/ptm-visao-geral"}})
    except RuntimeError as e:
        print(f"Kibana: formato pt-BR não aplicado ({str(e)[:120]})")
    for ident, nome in DATA_VIEWS.items():
        kb("POST", "/api/data_views/data_view", json={"override": True, "data_view": {
            "id": ident, "title": ident, "name": nome, "timeFieldName": "@timestamp"}})
    ndjson = "\n".join(json.dumps(o, ensure_ascii=False) for o in objetos_kibana()) + "\n"
    r = kb("POST", "/api/saved_objects/_import?overwrite=true",
           files={"file": ("paineis.ndjson", ndjson.encode("utf-8"), "application/ndjson")})
    if not r.get("success"):
        raise RuntimeError(f"importação no Kibana falhou: {json.dumps(r.get('errors'))[:600]}")
    print(f"Kibana: {r['successCount']} objetos importados -> "
          "http://127.0.0.1:5601/app/dashboards#/view/ptm-visao-geral")


if __name__ == "__main__":
    falhou = False
    for nome, func in (("Zabbix", painel_zabbix), ("Kibana", painel_kibana)):
        try:
            func()
        except Exception as e:
            falhou = True
            print(f"{nome}: ERRO {e}")
    sys.exit(1 if falhou else 0)
