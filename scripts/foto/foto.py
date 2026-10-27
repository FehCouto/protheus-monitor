#!/usr/bin/env python3
"""Tira uma captura de tela dos painéis do Kibana e do Zabbix (conferência visual).

Faz login com as credenciais locais do .env e grava PNGs em /capturas.
"""
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

KIBANA = "http://kibana:5601"
ZABBIX = "http://zabbix-web:8080"
SAIDA = "/capturas"


def id_painel_zabbix() -> str:
    def chama(metodo, params, token=None):
        cab = {"Content-Type": "application/json-rpc"}
        if token:
            cab["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(f"{ZABBIX}/api_jsonrpc.php", headers=cab, data=json.dumps(
            {"jsonrpc": "2.0", "method": metodo, "params": params, "id": 1}).encode())
        return json.load(urllib.request.urlopen(req, timeout=30))["result"]

    token = chama("user.login", {"username": "Admin", "password": os.environ["ZABBIX_ADMIN_PASSWORD"]})
    return chama("dashboard.get", {"filter": {"name": "Protheus - Visão geral"}, "output": ["dashboardid"]},
                 token)[0]["dashboardid"]


with sync_playwright() as p:
    navegador = p.chromium.launch(args=["--no-sandbox"])

    pagina = navegador.new_page(viewport={"width": 1600, "height": 2300})
    pagina.goto(f"{KIBANA}/login", wait_until="domcontentloaded")
    pagina.fill('[data-test-subj="loginUsername"]', "elastic")
    pagina.fill('[data-test-subj="loginPassword"]', os.environ["ELASTIC_PASSWORD"])
    pagina.click('[data-test-subj="loginSubmit"]')
    pagina.wait_for_url(lambda u: "/login" not in u, timeout=60000)
    pagina.goto(f"{KIBANA}/app/dashboards#/view/ptm-visao-geral", wait_until="domcontentloaded")
    pagina.wait_for_timeout(30000)
    pagina.screenshot(path=f"{SAIDA}/kibana.png")
    print("kibana.png gravado")

    pagina = navegador.new_page(viewport={"width": 1600, "height": 2000})
    pagina.goto(f"{ZABBIX}/index.php", wait_until="domcontentloaded")
    pagina.fill("#name", "Admin")
    pagina.fill("#password", os.environ["ZABBIX_ADMIN_PASSWORD"])
    pagina.click("#enter")
    pagina.wait_for_load_state("domcontentloaded")
    pagina.goto(f"{ZABBIX}/zabbix.php?action=dashboard.view&dashboardid={id_painel_zabbix()}",
                wait_until="domcontentloaded")
    pagina.wait_for_timeout(15000)
    pagina.screenshot(path=f"{SAIDA}/zabbix.png")
    print("zabbix.png gravado")

    pagina.goto(f"{ZABBIX}/zabbix.php?action=problem.view", wait_until="domcontentloaded")
    pagina.wait_for_timeout(5000)
    pagina.screenshot(path=f"{SAIDA}/zabbix-problemas.png")
    print("zabbix-problemas.png gravado")

    navegador.close()
