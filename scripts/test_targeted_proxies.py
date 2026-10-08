# -*- coding: utf-8 -*-
"""
Auditoría Enfocada: Proxies Elite, Geolocalizados (Argentina y Cono Sur) y Pruebas con SOCKS5h y Playwright
"""
import sys
import time
import json
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
}

def get_candidates():
    candidates = []
    
    # 1. ProxyScrape Argentina (HTTP y SOCKS5)
    for proto in ["http", "socks5"]:
        for country in ["AR", "CL", "UY", "BR"]:
            url = f"https://api.proxyscrape.com/v2/?request=getproxies&protocol={proto}&timeout=10000&country={country}&ssl=all&anonymity=all"
            try:
                r = requests.get(url, timeout=6)
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        if ":" in line.strip():
                            candidates.append((proto, line.strip(), country, "ProxyScrape"))
            except Exception:
                pass
                
    # 2. Geonode API (Proxies con metadatos de país y protocolo)
    try:
        for country in ["AR", "CL", "UY", "BR"]:
            url = f"https://proxylist.geonode.com/api/proxy-list?country={country}&limit=50&page=1&sort_by=lastChecked&sort_type=desc"
            r = requests.get(url, timeout=7)
            if r.status_code == 200:
                data = r.json().get("data", [])
                for item in data:
                    ip = item.get("ip")
                    port = item.get("port")
                    protocols = item.get("protocols", [])
                    for p in protocols:
                        candidates.append((p.lower(), f"{ip}:{port}", country, "Geonode"))
    except Exception as e:
        print(f"Nota: Geonode API arrojó {e}")

    # 3. Proxifly / GitHub listas con filtro elite
    url_elite = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=elite"
    try:
        r = requests.get(url_elite, timeout=6)
        if r.status_code == 200:
            for line in r.text.splitlines()[:50]:
                if ":" in line.strip():
                    candidates.append(("http", line.strip(), "GLOBAL_ELITE", "ProxyScrapeElite"))
    except Exception:
        pass

    return candidates

def test_single_proxy(proto, host_port, country, source):
    # Probar con socks5h para resolución DNS remota si es socks5
    actual_proto = "socks5h" if proto == "socks5" else proto
    proxy_url = f"{actual_proto}://{host_port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    
    t0 = time.time()
    res = {
        "proxy": proxy_url,
        "country": country,
        "source": source,
        "success": False,
        "latency": 0.0,
        "status_code": None,
        "detail": ""
    }
    
    try:
        r = requests.get(
            "https://numeracion.enacom.gob.ar/Numeracion.aspx",
            headers=headers,
            proxies=proxies,
            timeout=8.0,
            verify=False
        )
        res["latency"] = round(time.time() - t0, 2)
        res["status_code"] = r.status_code
        
        has_enacom = ("ENACOM" in r.text) or ("Numeración" in r.text) or ("ctl00" in r.text) or ("reCAPTCHA" in r.text)
        is_firewall = (r.status_code == 303) or ("firewall.enacom.gob.ar" in r.text) or ("firewall.enacom.gob.ar" in r.url)
        
        if has_enacom and not is_firewall:
            res["success"] = True
            res["detail"] = f"OK ({len(r.text)} bytes, form detectado)"
        elif is_firewall:
            res["detail"] = "Bloqueado por CheckPoint Firewall ENACOM (303)"
        else:
            res["detail"] = f"HTTP {r.status_code} pero sin contenido esperado"
            
    except requests.exceptions.ProxyError as pe:
        res["detail"] = f"ProxyError (No soporta CONNECT 443 / Rechazado): {str(pe)[:40]}"
    except requests.exceptions.ConnectTimeout:
        res["detail"] = "ConnectTimeout (>8s)"
    except requests.exceptions.ReadTimeout:
        res["detail"] = "ReadTimeout"
    except Exception as e:
        res["detail"] = f"{type(e).__name__}: {str(e)[:40]}"
        
    return res

def main():
    print("=" * 70, flush=True)
    print("AUDITORIA ENFOCADA: PROXIES GEOLOCALIZADOS (AR/LATAM) Y PROTOCOLO SOCKS5h", flush=True)
    print("=" * 70, flush=True)
    print("Recolectando candidatos especializados desde APIs públicas...", flush=True)
    
    candidates = get_candidates()
    # Eliminar duplicados exactos
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c[0], c[1])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)
            
    print(f"Total candidatos especializados obtenidos: {len(unique_candidates)}", flush=True)
    
    by_country = {}
    for _, _, country, _ in unique_candidates:
        by_country[country] = by_country.get(country, 0) + 1
    print(f"Distribución geográfica: {by_country}", flush=True)
    
    print("\nIniciando prueba concurrentemente con resolución DNS remota (socks5h)...", flush=True)
    
    results = []
    successes = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(test_single_proxy, proto, hp, country, src) for proto, hp, country, src in unique_candidates]
        done = 0
        for f in as_completed(futures):
            done += 1
            res = f.result()
            results.append(res)
            if res["success"]:
                successes.append(res)
                print(f"\n[!!! EXITO FUNCIONAL !!!] {res['proxy']} ({res['country']}) -> {res['latency']}s | {res['detail']}", flush=True)
            else:
                if done % 10 == 0 or "CheckPoint" in res["detail"] or res["status_code"] == 200:
                    print(f"  [{done}/{len(unique_candidates)}] {res['proxy']} ({res['country']}): {res['detail']}", flush=True)
                    
    print("\n" + "=" * 70, flush=True)
    print("RESUMEN DE LA AUDITORIA ENFOCADA", flush=True)
    print("=" * 70, flush=True)
    print(f"Total probados: {len(results)}")
    print(f"Total exitosos (lograron cargar ENACOM): {len(successes)}")
    
    if successes:
        print("\nPROXIES COMPROBADOS QUE FUNCIONAN:")
        for s in successes:
            print(f"  * {s['proxy']} [{s['country']}] - Latencia: {s['latency']}s")
            
    # Estadísticas de fallas
    faults = {}
    for r in results:
        if not r["success"]:
            # Agrupar primer palabra del detalle
            k = r["detail"].split(":")[0].split("(")[0].strip()
            faults[k] = faults.get(k, 0) + 1
            
    print("\nDesglose de motivos de falla:")
    for f_name, cnt in sorted(faults.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {f_name}: {cnt} ({cnt/len(results)*100:.1f}%)")

if __name__ == "__main__":
    main()
