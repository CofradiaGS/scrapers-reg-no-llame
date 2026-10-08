# -*- coding: utf-8 -*-
import sys
import time
import random
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings()

sources = [
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt',
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
    'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=2000&country=all&ssl=yes&anonymity=elite'
]

cands = []
for s in sources:
    try:
        r = requests.get(s, timeout=5)
        if r.status_code == 200:
            for l in r.text.splitlines():
                if ':' in l.strip() and not l.strip().startswith('#'):
                    cands.append(l.strip())
    except:
        pass

cands = list(set(cands))
random.shuffle(cands)
sample = cands[:200]
print(f"Total candidatos HTTPS desduplicados: {len(cands)}. Probando muestra de {len(sample)} en paralelo...", flush=True)

def test_p(p):
    t0 = time.time()
    try:
        r = requests.get(
            'https://numeracion.enacom.gob.ar/Numeracion.aspx', 
            proxies={'http': f'http://{p}', 'https': f'http://{p}'}, 
            timeout=4.0, 
            verify=False
        )
        lat = round(time.time() - t0, 2)
        if 'ENACOM' in r.text or 'Numeración' in r.text or 'ctl00' in r.text:
            return True, p, lat, "OK"
        elif r.status_code == 303 or 'firewall.enacom.gob.ar' in r.text:
            return False, p, lat, "CheckPoint Firewall Block (303)"
        else:
            return False, p, lat, f"HTTP {r.status_code} ({len(r.text)} bytes)"
    except Exception as e:
        return False, p, 0.0, type(e).__name__

vivos = []
reasons = {}

with ThreadPoolExecutor(max_workers=50) as executor:
    futures = [executor.submit(test_p, p) for p in sample]
    for f in as_completed(futures):
        ok, p, lat, reason = f.result()
        if ok:
            print(f"  [+] EXITO VIVO: {p} ({lat}s)", flush=True)
            vivos.append((p, lat))
        else:
            reasons[reason] = reasons.get(reason, 0) + 1

print("\n" + "=" * 50, flush=True)
print(f"RESULTADO: {len(vivos)} de {len(sample)} proxies HTTPS lograron conectar con ENACOM.", flush=True)
for r, c in sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"  - {r}: {c}", flush=True)
print("=" * 50, flush=True)
