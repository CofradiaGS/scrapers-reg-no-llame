# -*- coding: utf-8 -*-
import requests
import urllib3

urllib3.disable_warnings()

proxies_to_test = [
    'socks5://47.237.113.119:9080',
    'socks5://47.252.18.37:9098',
    'socks5://8.215.15.163:1337'
]

for p in proxies_to_test:
    print(f"Probando {p}...")
    try:
        r = requests.get('https://api.ipify.org?format=json', proxies={'http': p, 'https': p}, timeout=5, verify=False)
        print(f"  -> IP externa: {r.text.strip()}")
    except Exception as e:
        print(f"  -> Error IP: {type(e).__name__}: {e}")
        
    try:
        r_ena = requests.get('https://numeracion.enacom.gob.ar/Numeracion.aspx', proxies={'http': p, 'https': p}, timeout=8, verify=False)
        has_enacom = ("ENACOM" in r_ena.text) or ("Numeración" in r_ena.text) or ("ctl00" in r_ena.text)
        print(f"  -> ENACOM status: {r_ena.status_code}, len: {len(r_ena.text)}, ENACOM valido: {has_enacom}")
    except Exception as e:
        print(f"  -> Error ENACOM: {type(e).__name__}: {e}")
