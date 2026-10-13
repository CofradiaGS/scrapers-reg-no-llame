# -*- coding: utf-8 -*-
"""
Harvest y Test Exhaustivo de TODOS los Proxies de Argentina disponibles en la web.
1. Consulta todas las APIs y repositorios que publican proxies de Argentina.
2. Desduplica el 100% de los candidatos.
3. Evalúa cada uno contra ENACOM (https://numeracion.enacom.gob.ar/Numeracion.aspx)
   midiendo:
   - Capacidad de conexión y Handshake TLS (puerto 443)
   - Detección del formulario real de ENACOM
   - Latencia de respuesta
   - Descarga de Captcha
"""

import sys
import time
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

urllib3.disable_warnings()

SOURCES = [
    # ProxyScrape
    ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=AR&ssl=all&anonymity=all"),
    ("socks4", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=AR"),
    ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=AR"),
    # Geonode API
    ("geonode", "https://proxylist.geonode.com/api/proxy-list?country=AR&limit=500&page=1&sort_by=lastChecked&sort_type=desc"),
    ("geonode", "https://proxylist.geonode.com/api/proxy-list?country=AR&limit=500&page=2&sort_by=lastChecked&sort_type=desc"),
    # Proxy-list.download
    ("http", "https://www.proxy-list.download/api/v1/get?type=http&country=AR"),
    ("https", "https://www.proxy-list.download/api/v1/get?type=https&country=AR"),
    ("socks4", "https://www.proxy-list.download/api/v1/get?type=socks4&country=AR"),
    ("socks5", "https://www.proxy-list.download/api/v1/get?type=socks5&country=AR"),
    # GitHub Monosans
    ("all", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR.txt"),
    ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/http.txt"),
    ("socks4", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks4.txt"),
    ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks5.txt"),
    # Repositorios adicionales
    ("http", "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/country/AR.txt"),
    ("http", "https://raw.githubusercontent.com/casals-ar/proxy-list/main/ar.txt"),
    ("http", "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/ar.txt"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def harvest():
    print("=" * 70, flush=True)
    print("1. COSECHANDO TODOS LOS PROXIES DE ARGENTINA EN LA WEB", flush=True)
    print("=" * 70, flush=True)
    
    candidates = []
    for default_proto, url in SOURCES:
        try:
            r = requests.get(url, timeout=7)
            if r.status_code == 200:
                count = 0
                if default_proto == "geonode":
                    data = r.json().get("data", [])
                    for item in data:
                        ip = item.get("ip")
                        port = item.get("port")
                        protos = item.get("protocols", ["http"])
                        for p in protos:
                            candidates.append((p.lower(), f"{ip}:{port}"))
                            count += 1
                else:
                    for line in r.text.splitlines():
                        line = line.strip()
                        if ":" in line and not line.startswith("#"):
                            proto = default_proto
                            if "://" in line:
                                p_split = line.split("://")
                                proto = p_split[0]
                                host_port = p_split[1]
                            else:
                                host_port = line
                            
                            parts = host_port.split(":")
                            if len(parts) >= 2 and parts[1].isdigit():
                                candidates.append((proto, f"{parts[0]}:{parts[1]}"))
                                count += 1
                print(f"  * {url.split('/')[-1]}: {count} encontrados", flush=True)
        except Exception:
            pass

    # Desduplicar
    unique = list(set(candidates))
    print(f"\nTotal único absoluto de proxies argentinos recolectados: {len(unique)}", flush=True)
    
    proto_counts = defaultdict(int)
    for proto, _ in unique:
        proto_counts[proto] += 1
    for proto, cnt in proto_counts.items():
        print(f"    - {proto.upper():<7}: {cnt} proxies", flush=True)
        
    return unique

def test_proxy(item):
    proto, host_port = item
    # SOCKS5h para resolución de nombres remota
    actual_proto = "socks5h" if proto == "socks5" else proto
    proxy_url = f"{actual_proto}://{host_port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    
    t0 = time.time()
    res = {
        "proxy": proxy_url,
        "proto": proto,
        "host_port": host_port,
        "success": False,
        "latency": 0.0,
        "detail": "",
        "status_code": None,
        "captcha_ok": False
    }
    
    try:
        # Paso 1: Portada ENACOM
        r = requests.get(
            "https://numeracion.enacom.gob.ar/Numeracion.aspx",
            headers=HEADERS,
            proxies=proxies,
            timeout=8.0,
            verify=False
        )
        res["latency"] = round(time.time() - t0, 2)
        res["status_code"] = r.status_code
        
        has_enacom = ("ENACOM" in r.text or "Numeración" in r.text or "ctl00" in r.text or "reCAPTCHA" in r.text)
        is_firewall = (r.status_code == 303 or "firewall.enacom.gob.ar" in r.text)
        
        if has_enacom and not is_firewall:
            res["success"] = True
            res["detail"] = f"OK ({len(r.text)} bytes)"
            
            # Paso 2: Si cargó la portada, probar si puede descargar el Captcha
            try:
                r_cap = requests.get(
                    "https://numeracion.enacom.gob.ar/Captcha.aspx",
                    headers=HEADERS,
                    proxies=proxies,
                    timeout=8.0,
                    verify=False
                )
                if r_cap.status_code == 200 and "image" in r_cap.headers.get("Content-Type", ""):
                    res["captcha_ok"] = True
                    res["detail"] += " | Captcha OK"
                else:
                    res["detail"] += " | Captcha Falló"
            except Exception:
                res["detail"] += " | Captcha Timeout"
                
        elif is_firewall:
            res["detail"] = "Firewall CheckPoint ENACOM (303)"
        else:
            res["detail"] = f"HTTP {r.status_code}"
            
    except requests.exceptions.ProxyError:
        res["detail"] = "ProxyError (Sin soporte CONNECT 443)"
    except requests.exceptions.ConnectTimeout:
        res["detail"] = "ConnectTimeout (>8s)"
    except requests.exceptions.ReadTimeout:
        res["detail"] = "ReadTimeout (>8s)"
    except requests.exceptions.ConnectionError:
        res["detail"] = "ConnectionError (Host caído / Rechazado)"
    except Exception as e:
        res["detail"] = f"{type(e).__name__}"
        
    return res

def main():
    proxies = harvest()
    if not proxies:
        print("No se encontraron proxies argentinos.")
        return
        
    print("\n" + "=" * 70, flush=True)
    print(f"2. TESTEANDO AL 100% DE LOS PROXIES ARGENTINOS ({len(proxies)} PROXIES)", flush=True)
    print("Probando conectividad HTTPS a https://numeracion.enacom.gob.ar/...", flush=True)
    print("=" * 70, flush=True)
    
    t_start = time.time()
    results = []
    vivos = []
    vivos_con_captcha = []
    motivos = defaultdict(int)
    
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(test_proxy, p) for p in proxies]
        done = 0
        for f in as_completed(futures):
            done += 1
            res = f.result()
            results.append(res)
            
            if res["success"]:
                vivos.append(res)
                if res["captcha_ok"]:
                    vivos_con_captcha.append(res)
                print(f"  [+] ¡FUNCIONA! {res['proxy']} -> Latencia: {res['latency']}s | {res['detail']}", flush=True)
            else:
                motivos[res["detail"]] += 1
                
            if done % 15 == 0 or done == len(proxies):
                print(f"  ... Evaluados {done}/{len(proxies)} proxies...", flush=True)
                
    dur = round(time.time() - t_start, 1)
    
    print("\n" + "=" * 70, flush=True)
    print("INFORME DEFINITIVO DE DISPONIBILIDAD REAL AHORA MISMO", flush=True)
    print("=" * 70, flush=True)
    print(f"Tiempo total de testeo: {dur} segundos")
    print(f"Total proxies de Argentina evaluados: {len(proxies)}")
    print(f"Total proxies de Argentina VIVOS que conectan a ENACOM: {len(vivos)}")
    print(f"Total proxies de Argentina 100% APTOS (Cargan Formulario + Captcha): {len(vivos_con_captcha)}")
    
    if vivos:
        print("\n--- DETALLE DE PROXIES UTILIZABLES EN ESTE INSTANTE ---")
        for v in vivos:
            print(f"  * {v['proxy']:<35} | Latencia: {v['latency']}s | {v['detail']}")
    else:
        print("\nNingún proxy público argentino logró superar la prueba en este segundo.")
        
    print("\nDesglose de motivos por los que falló el resto:")
    for mot, cnt in sorted(motivos.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {mot:<45}: {cnt} ({cnt/len(proxies)*100:.1f}%)")

if __name__ == "__main__":
    main()
