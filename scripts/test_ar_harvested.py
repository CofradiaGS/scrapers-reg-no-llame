# -*- coding: utf-8 -*-
import sys
import time
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings()

proxies_to_test = [
    ("http", "201.190.178.191:8080"),
    ("http", "198.12.37.18:8080"),
    ("http", "190.7.19.110:8080"),
    ("http", "131.0.235.26:55555"),
    ("https", "181.209.114.220:8080"),
    ("socks4", "198.12.37.25:1080"),
    ("http", "45.229.17.74:999"),
    ("http", "181.79.57.57:8080"),
    ("socks4", "198.12.37.25:1081"),
    ("socks4", "200.63.95.17:1085"),
    ("http", "200.110.218.10:999"),
    ("socks4", "200.43.43.124:4145"),
    ("http", "190.229.164.58:9080"),
    ("http", "181.209.125.237:999"),
    ("socks4", "190.228.33.45:1085"),
    ("http", "190.229.64.252:443"),
    ("socks5", "190.229.64.252:443"),
    ("http", "181.192.2.23:8080"),
    ("http", "181.118.96.115:8080"),
    ("http", "181.166.19.167:8080"),
    ("http", "181.164.120.245:8080"),
    ("http", "181.209.92.100:8080"),
    ("http", "181.192.17.15:8080"),
    ("http", "181.118.158.20:8080"),
    ("http", "181.167.142.126:8080"),
    ("http", "181.165.236.4:8080"),
    ("http", "181.209.91.130:8080"),
    ("http", "181.164.152.93:8080"),
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def test(proto, hp):
    actual_proto = "socks5h" if proto == "socks5" else proto
    proxy_url = f"{actual_proto}://{hp}"
    t0 = time.time()
    try:
        r = requests.get(
            "https://numeracion.enacom.gob.ar/Numeracion.aspx",
            headers=headers,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=7.0,
            verify=False
        )
        lat = round(time.time() - t0, 2)
        if ("ENACOM" in r.text or "Numeración" in r.text or "ctl00" in r.text) and r.status_code == 200:
            return True, proxy_url, lat, f"OK ({len(r.text)} bytes)"
        elif r.status_code == 303 or "firewall.enacom.gob.ar" in r.text:
            return False, proxy_url, lat, "CheckPoint Firewall Block (303)"
        else:
            return False, proxy_url, lat, f"HTTP {r.status_code} ({len(r.text)}b)"
    except Exception as e:
        return False, proxy_url, 0.0, type(e).__name__

print("Probando concurrentemente con 20 hilos...")
vivos = []
with ThreadPoolExecutor(max_workers=20) as ex:
    futures = [ex.submit(test, proto, hp) for proto, hp in proxies_to_test]
    for f in as_completed(futures):
        ok, p, lat, desc = f.result()
        if ok:
            print(f"  [+] VIVO Y APTO ENACOM: {p} ({lat}s) - {desc}")
            vivos.append((p, lat))
        else:
            # print(f"  [-] {p}: {desc}")
            pass

print(f"\nTotal funcionales detectados: {len(vivos)}")
for p, lat in vivos:
    print(f"  * {p} ({lat}s)")
