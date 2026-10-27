#!/usr/bin/env python3
"""Coletor do protheus-monitor.

Único componente que fala com o Protheus. Roda as consultas definidas em
consultas/*.yml (somente leitura, uma por vez), grava o resultado no
Elasticsearch e envia os números para o Zabbix (itens trapper).
"""
import json
import logging
import os
import re
import signal
import socket
import struct
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pymssql
import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coletor")

TZ = ZoneInfo(os.environ.get("TZ", "America/Sao_Paulo"))
DIR_CONSULTAS = Path("/app/consultas")
ARQ_ESTADO = Path("/app/estado/estado.json")

ES_URL = os.environ["ES_URL"]
ES_AUTH = ("elastic", os.environ["ELASTIC_PASSWORD"])
ZBX_URL = os.environ["ZABBIX_URL"]
ZBX_SERVER = os.environ["ZABBIX_SERVER"]
ZBX_SENHA = os.environ["ZABBIX_ADMIN_PASSWORD"]
ZBX_HOST = "protheus-producao"
ZBX_NOME = "Protheus Produção"
REST_HOST = os.environ["PROTHEUS_REST_HOST"]
REST_PORT = os.environ["PROTHEUS_REST_PORT"]
CENARIO_REST = "REST Protheus"
# Usuário do Protheus dedicado ao monitoramento; sem ele as consultas "rest" ficam desligadas.
REST_USER = os.environ.get("PROTHEUS_REST_USER", "")
REST_SENHA = os.environ.get("PROTHEUS_REST_PASSWORD", "")

# Proteções do banco de produção: nenhuma consulta roda com intervalo menor que
# INTERVALO_MIN nem por mais que TIMEOUT_SQL segundos.
INTERVALO_MIN = 60
TIMEOUT_SQL = 30
TIMEOUT_REST = 20
RETENCAO_DIAS = int(os.environ.get("RETENCAO_DIAS", "90"))

PROIBIDO = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|grant|revoke|into)\b", re.I
)
TIPOS = ("metricas", "eventos", "amostras", "rest")

parar = False


def encerra(*_):
    global parar
    parar = True


signal.signal(signal.SIGTERM, encerra)
signal.signal(signal.SIGINT, encerra)


# --------------------------------------------------------------------------- consultas
def valida_sql(ident: str, sql: str) -> None:
    """Recusa qualquer coisa que não seja SELECT com NOLOCK."""
    sem_literais = re.sub(r"'[^']*'", "''", sql)
    if not re.match(r"\s*(select|with)\b", sem_literais, re.I):
        raise ValueError(f"{ident}: a consulta precisa começar com SELECT")
    achou = PROIBIDO.search(sem_literais)
    if achou:
        raise ValueError(f"{ident}: palavra proibida na consulta: {achou.group(1)}")
    if "nolock" not in sem_literais.lower():
        raise ValueError(f"{ident}: toda tabela precisa de WITH (NOLOCK)")


def carrega_consultas() -> list[dict]:
    consultas, vistos = [], set()
    for arq in sorted(DIR_CONSULTAS.glob("*.yml")):
        for c in yaml.safe_load(arq.read_text(encoding="utf-8")) or []:
            ident = c["id"]
            if ident in vistos:
                raise ValueError(f"id de consulta duplicado: {ident}")
            vistos.add(ident)
            if c["tipo"] not in TIPOS:
                raise ValueError(f"{ident}: tipo inválido {c['tipo']}")
            if c["tipo"] == "rest":
                if not re.fullmatch(r"/[\w./?=&-]+", c["caminho"]):
                    raise ValueError(f"{ident}: caminho inválido {c['caminho']}")
                if not REST_USER:
                    log.info("%s ignorada: PROTHEUS_REST_USER não definido no .env", ident)
                    continue
            else:
                valida_sql(ident, c["sql"])
            c["intervalo"] = max(int(c.get("intervalo", 300)), INTERVALO_MIN)
            c["_proxima"] = 0.0
            c["_falhas"] = 0
            consultas.append(c)
    log.info("%d consultas carregadas", len(consultas))
    return consultas


# --------------------------------------------------------------------------- SQL Server
class Banco:
    def __init__(self):
        self.con = None

    def conecta(self):
        self.con = pymssql.connect(
            server=os.environ["MSSQL_HOST"],
            port=int(os.environ["MSSQL_PORT"]),
            user=os.environ["MSSQL_USER"],
            password=os.environ["MSSQL_PASSWORD"],
            database=os.environ["MSSQL_DATABASE"],
            timeout=TIMEOUT_SQL,
            login_timeout=15,
            appname="protheus-monitor-local",
            autocommit=True,
        )
        cur = self.con.cursor()
        # Cinto e suspensório: mesmo que uma consulta esqueça o NOLOCK, a sessão
        # não segura lock, desiste rápido de esperar e perde qualquer deadlock.
        cur.execute(
            "SET NOCOUNT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED; "
            "SET LOCK_TIMEOUT 5000; SET DEADLOCK_PRIORITY LOW;"
        )
        cur.close()

    def fecha(self):
        try:
            if self.con:
                self.con.close()
        except Exception:
            pass
        self.con = None

    def executa(self, sql: str, params: dict | None = None) -> tuple[list[dict], int]:
        if self.con is None:
            self.conecta()
        inicio = time.monotonic()
        try:
            cur = self.con.cursor(as_dict=True)
            cur.execute(sql, params) if params else cur.execute(sql)
            linhas = cur.fetchall()
            cur.close()
        except Exception:
            self.fecha()
            raise
        return linhas, int((time.monotonic() - inicio) * 1000)


def limpa(valor):
    """Normaliza um valor vindo do Protheus (padding, \\0 de memo, Decimal)."""
    if isinstance(valor, (bytes, bytearray)):
        valor = bytes(valor).decode("cp1252", errors="replace")
    if isinstance(valor, str):
        return valor.replace("\x00", "").rstrip()
    if isinstance(valor, Decimal):
        valor = float(valor)
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=TZ).isoformat()
    return valor


def monta_timestamp(doc: dict, cfg: dict | None) -> str:
    """Monta @timestamp a partir de data AAAAMMDD e hora HH:MM[:SS] do Protheus."""
    agora = datetime.now(TZ)
    if not cfg:
        return agora.isoformat()
    data = str(doc.get(cfg["data"], "")).strip()
    hora = str(doc.get(cfg.get("hora", ""), "")).strip() or "00:00"
    for fmt in ("%Y%m%d %H:%M:%S", "%Y%m%d %H:%M"):
        try:
            return datetime.strptime(f"{data} {hora}", fmt).replace(tzinfo=TZ).isoformat()
        except ValueError:
            continue
    return agora.isoformat()


# --------------------------------------------------------------------------- REST do Protheus
class Rest:
    """Só GET. Uma tentativa por ciclo: timeout não é reenviado, o recuo exponencial cuida do resto."""

    def __init__(self):
        self.base = f"http://{REST_HOST}:{REST_PORT}/rest"
        self.token = None
        self.validade = 0.0

    def autentica(self) -> str:
        # O token vale só para este host (produção e homologação não compartilham).
        if self.token and time.time() < self.validade:
            return self.token
        r = requests.post(f"{self.base}/api/oauth2/v1/token", params={"grant_type": "password"},
                          headers={"username": REST_USER, "password": REST_SENHA}, timeout=TIMEOUT_REST)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"REST token: http {r.status_code}")
        j = r.json()
        self.token = j["access_token"]
        self.validade = time.time() + int(j.get("expires_in", 3600)) - 120
        return self.token

    def get(self, caminho: str) -> tuple[dict, int]:
        token = self.autentica()
        inicio = time.monotonic()
        r = requests.get(f"{self.base}{caminho}", headers={"Authorization": f"Bearer {token}"},
                         timeout=TIMEOUT_REST)
        if r.status_code == 401:
            self.token = None
        if r.status_code != 200:
            raise RuntimeError(f"REST GET {caminho}: http {r.status_code} {r.text[:200]}")
        dados = r.json()
        # Os endpoints customizados respondem 200 mesmo com erro; o aviso vem no corpo.
        if dados.get("hasError"):
            raise RuntimeError(f"REST GET {caminho}: {str(dados.get('errorMessage'))[:200]}")
        return dados, int((time.monotonic() - inicio) * 1000)


# --------------------------------------------------------------------------- Elasticsearch
def es(metodo: str, caminho: str, **kw):
    r = requests.request(metodo, f"{ES_URL}{caminho}", auth=ES_AUTH, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Elasticsearch {metodo} {caminho}: {r.status_code} {r.text[:300]}")
    return r.json()


def es_prepara():
    es(
        "PUT",
        "/_index_template/protheus",
        json={
            "index_patterns": ["protheus-*"],
            "template": {
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {
                    "dynamic_templates": [
                        {
                            "textos": {
                                "match_mapping_type": "string",
                                "mapping": {"type": "keyword", "ignore_above": 1024},
                            }
                        }
                    ],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "mensagem": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                        },
                        "detalhe": {"type": "text"},
                    },
                },
            },
        },
    )


def es_bulk(indice: str, docs: list[dict], chave: str | None = None):
    if not docs:
        return
    linhas = []
    for d in docs:
        acao = {"index": {"_index": indice}}
        if chave:
            acao["index"]["_id"] = str(d[chave])
        linhas.append(json.dumps(acao))
        linhas.append(json.dumps(d, ensure_ascii=False, default=str))
    r = es("POST", "/_bulk", data=("\n".join(linhas) + "\n").encode("utf-8"),
           headers={"Content-Type": "application/x-ndjson"})
    if r.get("errors"):
        erro = next((i["index"]["error"] for i in r["items"] if "error" in i["index"]), None)
        raise RuntimeError(f"bulk em {indice} com erros: {erro}")


def es_retencao():
    corte = (datetime.now(TZ) - timedelta(days=RETENCAO_DIAS)).isoformat()
    r = es("POST", "/protheus-*/_delete_by_query?ignore_unavailable=true&conflicts=proceed",
           json={"query": {"range": {"@timestamp": {"lt": corte}}}})
    log.info("retenção (%d dias): %d documentos removidos", RETENCAO_DIAS, r.get("deleted", 0))


# --------------------------------------------------------------------------- Zabbix
def chave_item(consulta: dict, coluna: str) -> str:
    return f"protheus.{consulta['id']}.{coluna}"


def zbx_envia(pares: list[tuple[str, object]]):
    """Envia valores pelo protocolo do zabbix_sender (itens trapper)."""
    pares = [(k, v) for k, v in pares if v is not None]
    if not pares:
        return
    agora = int(time.time())
    corpo = json.dumps({
        "request": "sender data",
        "data": [{"host": ZBX_HOST, "key": k, "value": str(v), "clock": agora} for k, v in pares],
    }).encode()
    pacote = b"ZBXD\x01" + struct.pack("<II", len(corpo), 0) + corpo
    with socket.create_connection((ZBX_SERVER, 10051), timeout=10) as s:
        s.sendall(pacote)
        resp = b""
        while True:
            parte = s.recv(4096)
            if not parte:
                break
            resp += parte
    info = json.loads(resp[13:].decode()).get("info", "")
    falhou = re.search(r"failed: (\d+)", info)
    if falhou and int(falhou.group(1)):
        log.warning("zabbix recusou %s de %d valores (%s)", falhou.group(1), len(pares), info)


class Zabbix:
    def __init__(self):
        self.url = f"{ZBX_URL}/api_jsonrpc.php"
        self.token = None

    def chama(self, metodo: str, params):
        cab = {"Content-Type": "application/json-rpc"}
        if self.token:
            cab["Authorization"] = f"Bearer {self.token}"
        r = requests.post(self.url, json={"jsonrpc": "2.0", "method": metodo, "params": params, "id": 1},
                          headers=cab, timeout=30)
        j = r.json()
        if "error" in j:
            raise RuntimeError(f"zabbix {metodo}: {j['error'].get('data') or j['error']}")
        return j["result"]

    def login(self):
        self.token = None
        try:
            self.token = self.chama("user.login", {"username": "Admin", "password": ZBX_SENHA})
            return
        except RuntimeError:
            pass
        # Primeira subida: troca a senha padrão de fábrica pela do .env.
        self.token = self.chama("user.login", {"username": "Admin", "password": "zabbix"})
        usuario = self.chama("user.get", {"filter": {"username": "Admin"}, "output": ["userid"]})[0]
        self.chama("user.update", {"userid": usuario["userid"], "current_passwd": "zabbix", "passwd": ZBX_SENHA})
        log.info("senha padrão do Admin do Zabbix substituída pela do .env")
        # Trocar a senha encerra a sessão em uso.
        self.token = None
        self.token = self.chama("user.login", {"username": "Admin", "password": ZBX_SENHA})

    def garante_host(self) -> tuple[str, str]:
        grupo = self.chama("hostgroup.get", {"filter": {"name": "Protheus"}, "output": ["groupid"]})
        gid = grupo[0]["groupid"] if grupo else self.chama("hostgroup.create", {"name": "Protheus"})["groupids"][0]
        host = self.chama("host.get", {"filter": {"host": ZBX_HOST}, "output": ["hostid"],
                                       "selectInterfaces": ["interfaceid"]})
        if host:
            return host[0]["hostid"], host[0]["interfaces"][0]["interfaceid"]
        hid = self.chama("host.create", {
            "host": ZBX_HOST, "name": ZBX_NOME, "groups": [{"groupid": gid}],
            "interfaces": [{"type": 1, "main": 1, "useip": 0, "ip": "", "dns": REST_HOST, "port": "10050"}],
        })["hostids"][0]
        iface = self.chama("hostinterface.get", {"hostids": hid, "output": ["interfaceid"]})[0]["interfaceid"]
        return hid, iface

    def garante_item(self, hid: str, item: dict):
        existe = self.chama("item.get", {"hostids": hid, "filter": {"key_": item["key_"]}, "output": ["itemid"]})
        if existe:
            mudanca = {k: v for k, v in item.items() if k in ("name", "units", "description", "tags")}
            self.chama("item.update", {"itemid": existe[0]["itemid"], **mudanca})
        else:
            self.chama("item.create", {"hostid": hid, **item})

    def garante_gatilho(self, hid: str, g: dict):
        existe = self.chama("trigger.get", {"hostids": hid, "filter": {"description": g["description"]},
                                            "output": ["triggerid"]})
        if existe:
            self.chama("trigger.update", {"triggerid": existe[0]["triggerid"], **g})
        else:
            self.chama("trigger.create", g)

    def prepara(self, consultas: list[dict]):
        self.login()
        hid, iface = self.garante_host()

        def trapper(chave, nome, unidade="", area="coletor", texto=False):
            self.garante_item(hid, {
                "key_": chave, "name": nome, "type": 2, "value_type": 4 if texto else 0, "units": unidade,
                "tags": [{"tag": "area", "value": area}],
            })

        trapper("protheus.coletor.ciclo", "Coletor: batimento")
        trapper("protheus.coletor.falhas", "Coletor: consultas com falha")
        trapper("protheus.sql.latencia_ms", "SQL Server: tempo de resposta", "ms", "banco")
        trapper("protheus.sql.conecta", "SQL Server: conexão OK (1/0)", "", "banco")
        for c in consultas:
            trapper(f"protheus.coletor.duracao_ms[{c['id']}]", f"Coletor: duração da consulta {c['id']}", "ms")
            if c["tipo"] not in ("metricas", "rest"):
                continue
            for coluna, m in c.get("metricas", {}).items():
                trapper(chave_item(c, coluna), m["nome"], m.get("unidade", ""), c.get("area", "protheus"))

        # Checagens que o próprio Zabbix faz, sem passar pelo banco.
        sql_host, sql_port = os.environ["MSSQL_HOST"], os.environ["MSSQL_PORT"]
        chave_sql = f"net.tcp.service.perf[tcp,{sql_host},{sql_port}]"
        chave_rest = f"net.tcp.service.perf[tcp,,{REST_PORT}]"
        for chave, nome in ((chave_sql, "Porta do SQL Server: tempo de conexão"),
                            (chave_rest, "Porta do REST: tempo de conexão")):
            self.garante_item(hid, {"key_": chave, "name": nome, "type": 3, "value_type": 0, "units": "s",
                                    "delay": "1m", "interfaceid": iface,
                                    "tags": [{"tag": "area", "value": "disponibilidade"}]})
        if not self.chama("httptest.get", {"hostids": hid, "filter": {"name": CENARIO_REST}, "output": ["httptestid"]}):
            self.chama("httptest.create", {
                "name": CENARIO_REST, "hostid": hid, "delay": "1m", "retries": 2,
                "steps": [{"name": "raiz", "no": 1, "url": f"http://{REST_HOST}:{REST_PORT}/rest/",
                           "status_codes": "200", "timeout": "15s"}],
            })

        h = f"/{ZBX_HOST}/"
        gatilhos = [
            {"description": "REST do Protheus fora do ar", "priority": 5,
             "expression": f"last({h}web.test.fail[{CENARIO_REST}])<>0"},
            {"description": "REST do Protheus lento (mais de 5s)", "priority": 2,
             "expression": f"min({h}web.test.time[{CENARIO_REST},raiz,resp],5m)>5"},
            {"description": "Porta do SQL Server do Protheus inacessível", "priority": 5,
             "expression": f"max({h}{chave_sql},3m)=0"},
            {"description": "Coletor não consegue conectar no SQL Server", "priority": 4,
             "expression": f"max({h}protheus.sql.conecta,10m)=0"},
            {"description": "Coletor parado (sem dados há 15 min)", "priority": 4,
             "expression": f"nodata({h}protheus.coletor.ciclo,15m)=1"},
            {"description": "SQL Server lento para o coletor (mais de 2s)", "priority": 2,
             "expression": f"min({h}protheus.sql.latencia_ms,10m)>2000"},
        ]
        for c in consultas:
            for g in c.get("gatilhos", []):
                expressao = re.sub(r"\{item:(\w+)\}", lambda m: h + chave_item(c, m.group(1)), g["expressao"])
                gatilhos.append({"description": g["nome"], "priority": g.get("severidade", 2),
                                 "expression": expressao, "comments": g.get("comentario", "")})
        for g in gatilhos:
            self.garante_gatilho(hid, {**g, "manual_close": 1})

        # O host de fábrica "Zabbix server" espera um agente que não existe aqui.
        padrao = self.chama("host.get", {"filter": {"host": "Zabbix server"}, "output": ["hostid", "status"]})
        if padrao and padrao[0]["status"] == "0":
            self.chama("host.update", {"hostid": padrao[0]["hostid"], "status": 1})
        log.info("zabbix preparado: host %s, %d gatilhos", ZBX_HOST, len(gatilhos))


# --------------------------------------------------------------------------- execução
def carrega_estado() -> dict:
    try:
        return json.loads(ARQ_ESTADO.read_text())
    except Exception:
        return {}


def salva_estado(estado: dict):
    tmp = ARQ_ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado))
    tmp.replace(ARQ_ESTADO)


def roda_metricas(db: Banco, c: dict) -> int:
    linhas, ms = db.executa(c["sql"])
    if not linhas:
        return ms
    linha = {k: limpa(v) for k, v in linhas[0].items()}
    zbx_envia([(chave_item(c, col), linha.get(col)) for col in c.get("metricas", {})])
    es_bulk("protheus-metricas", [{"@timestamp": datetime.now(TZ).isoformat(), "consulta": c["id"],
                                   "area": c.get("area", "protheus"), c["id"]: linha}])
    return ms


def aplica_rotulos(doc: dict, rotulos: dict):
    for campo, mapa in rotulos.items():
        doc[f"{campo}_desc"] = mapa.get(str(doc.get(campo, "")), "Outro")


def roda_eventos(db: Banco, c: dict, estado: dict) -> int:
    chave = c["chave"]
    desde = estado.get(c["id"])
    if desde is None:
        maximo, _ = db.executa(f"SELECT MAX(R_E_C_N_O_) AS m FROM {c['tabela']} WITH (NOLOCK)")
        desde = max(int(maximo[0]["m"] or 0) - int(c.get("carga_inicial", 2000)), 0)
    # "reler" volta alguns registros para pegar mudança de status de linhas recentes.
    linhas, ms = db.executa(c["sql"], {"desde": max(desde - int(c.get("reler", 0)), 0)})
    docs = []
    for linha in linhas:
        doc = {k: limpa(v) for k, v in linha.items()}
        aplica_rotulos(doc, c.get("rotulos", {}))
        doc["@timestamp"] = monta_timestamp(doc, c.get("timestamp"))
        docs.append(doc)
    es_bulk(c["indice"], docs, chave)
    if docs:
        estado[c["id"]] = max(desde, max(int(d[chave]) for d in docs))
        salva_estado(estado)
    return ms


def roda_amostras(db: Banco, c: dict) -> int:
    linhas, ms = db.executa(c["sql"])
    agora = datetime.now(TZ).isoformat()
    es_bulk(c["indice"], [{"@timestamp": agora, **{k: limpa(v) for k, v in l.items()}} for l in linhas])
    return ms


def roda_rest(rest: Rest, c: dict) -> int:
    dados, ms = rest.get(c["caminho"])
    agora = datetime.now(TZ).isoformat()
    linha = dados[c["raiz"]] if c.get("raiz") else dados
    zbx_envia([(chave_item(c, col), linha.get(col)) for col in c.get("metricas", {})])
    es_bulk("protheus-metricas", [{"@timestamp": agora, "consulta": c["id"],
                                   "area": c.get("area", "protheus"), c["id"]: linha}])
    # "listas": campos do JSON que são listas de objetos; cada item vira um documento.
    for campo, indice in c.get("listas", {}).items():
        es_bulk(indice, [{"@timestamp": agora, "lista": campo, **item} for item in dados.get(campo, [])])
    return ms


def espera(descricao: str, func, tentativas: int = 60, pausa: int = 10) -> bool:
    for n in range(1, tentativas + 1):
        if parar:
            return False
        try:
            func()
            return True
        except Exception as e:
            log.info("aguardando %s (%d/%d): %s", descricao, n, tentativas, str(e)[:160])
            time.sleep(pausa)
    return False


def main():
    consultas = carrega_consultas()
    if not consultas:
        # Pasta vazia é o bind mount que não montou: o Docker Desktop religa o container antes
        # de a integração com o WSL ficar pronta. Sair faz o Docker religar e montar de novo.
        time.sleep(30)
        sys.exit(f"nenhuma consulta em {DIR_CONSULTAS}")
    if not espera("Elasticsearch", es_prepara):
        sys.exit("Elasticsearch não respondeu")
    zbx = Zabbix()
    zbx_ok = espera("Zabbix", lambda: zbx.prepara(consultas), tentativas=40, pausa=15)
    if not zbx_ok:
        log.warning("Zabbix não ficou pronto; seguindo só com o Elasticsearch")

    db = Banco()
    rest = Rest()
    estado = carrega_estado()
    proximo_batimento = proxima_retencao = 0.0

    while not parar:
        agora = time.time()

        if agora >= proximo_batimento:
            proximo_batimento = agora + 60
            conecta, latencia = 1, None
            try:
                _, latencia = db.executa("SELECT 1 AS ok")
            except Exception as e:
                conecta = 0
                log.error("SQL Server indisponível: %s", str(e)[:200])
            falhando = sum(1 for c in consultas if c["_falhas"])
            try:
                zbx_envia([("protheus.coletor.ciclo", 1), ("protheus.coletor.falhas", falhando),
                           ("protheus.sql.conecta", conecta), ("protheus.sql.latencia_ms", latencia)])
            except Exception as e:
                log.warning("zabbix sem resposta: %s", str(e)[:160])

        if agora >= proxima_retencao:
            proxima_retencao = agora + 86400
            try:
                es_retencao()
            except Exception as e:
                log.warning("retenção falhou: %s", str(e)[:160])

        for c in consultas:
            if parar or time.time() < c["_proxima"]:
                continue
            try:
                if c["tipo"] == "metricas":
                    ms = roda_metricas(db, c)
                elif c["tipo"] == "eventos":
                    ms = roda_eventos(db, c, estado)
                elif c["tipo"] == "rest":
                    ms = roda_rest(rest, c)
                else:
                    ms = roda_amostras(db, c)
                c["_falhas"] = 0
                c["_proxima"] = time.time() + c["intervalo"]
                zbx_envia([(f"protheus.coletor.duracao_ms[{c['id']}]", ms)])
                log.info("ok %-22s %5d ms", c["id"], ms)
            except Exception as e:
                # Recuo exponencial: consulta com problema não fica martelando a produção.
                c["_falhas"] += 1
                recuo = min(c["intervalo"] * 2 ** c["_falhas"], 3600)
                c["_proxima"] = time.time() + recuo
                log.error("falha %s (tentativa %d, próxima em %ds): %s", c["id"], c["_falhas"], recuo, str(e)[:300])

        time.sleep(5)

    db.fecha()
    log.info("coletor encerrado")


if __name__ == "__main__":
    main()
