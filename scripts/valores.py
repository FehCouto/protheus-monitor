#!/usr/bin/env python3
"""Mostra no terminal o último valor de cada item do Zabbix e os problemas em aberto."""
import sys
import time

sys.path.insert(0, "/app")
import coletor  # noqa: E402

z = coletor.Zabbix()
z.login()
hid = z.chama("host.get", {"filter": {"host": coletor.ZBX_HOST}, "output": ["hostid"]})[0]["hostid"]
itens = z.chama("item.get", {"hostids": hid, "webitems": True, "sortfield": "name",
                             "output": ["name", "key_", "lastvalue", "lastclock", "units", "state", "error"]})
agora = time.time()
print(f"{'ITEM':<58} {'VALOR':>14}  IDADE")
for i in itens:
    if i["key_"].startswith("protheus.coletor.duracao_ms"):
        continue
    idade = "sem dado" if i["lastclock"] == "0" else f"{int(agora - int(i['lastclock']))}s"
    erro = f"  ERRO: {i['error'][:60]}" if i["state"] == "1" else ""
    print(f"{i['name'][:58]:<58} {i['lastvalue'][:10] + ' ' + i['units']:>14}  {idade}{erro}")

problemas = z.chama("problem.get", {"hostids": hid, "output": ["name", "severity", "clock"], "sortfield": "eventid"})
severidade = ["não classificado", "informação", "atenção", "média", "alta", "desastre"]
print(f"\nPROBLEMAS EM ABERTO: {len(problemas)}")
for p in problemas:
    print(f"  [{severidade[int(p['severity'])]}] {p['name']}")
