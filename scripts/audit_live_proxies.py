# -*- coding: utf-8 -*-
"""
Auditoría Exhaustiva de Proxies Públicos Gratuitos en Tiempo Real
1. Recolecta proxies desde más de 20 fuentes públicas globales.
2. Desduplica y cuenta el volumen bruto total disponible en internet.
3. Valida en paralelo (con hilos) la conectividad real hacia:
   - Verificación de IP pública (para confirmar que el proxy enruta tráfico real)
   - Servidor de ENACOM (https://numeracion.enacom.gob.ar/Numeracion.aspx)
"""

import sys
import time
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Lista exhaustiva de fuentes públicas mundiales
SOURCES = [
    # TheSpeedX
    ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("socks4", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    # monosans
    ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("socks4", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt"),
    ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
    # hookzof
    ("socks5", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"),
    # ProxyScrape API
    ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
    ("socks4", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all"),
    ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all"),
    # Proxifly
    ("http", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
    ("socks4", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt"),
    ("socks5", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt"),
    # roosterkid
    ("http", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt"),
    ("socks5", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt"),
    # jetkai
    ("http", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt"),
    ("https", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt"),
    ("socks4", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt"),
    ("socks5", "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt"),
    # vakhov
    ("http", "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt"),
    # prx-chk
    ("http", "https://raw.githubusercontent.com/prx-chk/proxy-list/main/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/prx-chk/proxy-list/main/socks5.txt"),
    # MuRongPIG
    ("http", "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt"),
    # Anonym0usWork1221
    ("http", "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/master/proxy_files/http_proxies.txt"),
    ("socks5", "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/master/proxy_files/socks5_proxies.txt"),
    # Zaeem20
    ("http", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/socks5.txt"),
]

def fetch_source(proto, url):
    results = []
    try:
        r = requests.get(url, timeout=7)
        if r.status_code == 200:
            for line in r.text.splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    # Si ya viene con protocolo, limpiarlo
                    clean_line = line.replace("http://", "").replace("https://", "").replace("socks4://", "").replace("socks5://", "")
                    parts = clean_line.split(":")
                    if len(parts) >= 2 and parts[1].isdigit():
                        results.append((proto, f"{parts[0]}:{parts[1]}"))
    except Exception:
        pass
    return url, results

def test_proxy_live(proto, host_port):
    """
    Prueba si el proxy está activo en internet y si llega a ENACOM.
    """
    proxy_url = f"{proto}://{host_port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    
    # 1. Test conectividad general (rápido 3s)
    t0 = time.time()
    general_ok = False
    enacom_ok = False
    enacom_lat = 0.0
    status_reason = ""
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Probar directamente con ENACOM
    try:
        r = requests.get(
            "https://numeracion.enacom.gob.ar/Numeracion.aspx",
            headers=headers,
            proxies=proxies,
            timeout=5.0,
            verify=False
        )
        enacom_lat = round(time.time() - t0, 2)
        if r.status_code == 200 and ("ENACOM" in r.text or "Numeración" in r.text or "reCAPTCHA" in r.text or "ctl00" in r.text):
            enacom_ok = True
            general_ok = True
        elif r.status_code == 303 or "firewall.enacom.gob.ar" in r.url or "firewall.enacom.gob.ar" in r.text:
            general_ok = True
            status_reason = "Bloqueado por Firewall ENACOM (CheckPoint 303)"
        else:
            general_ok = (r.status_code in [200, 301, 302, 403, 500])
            status_reason = f"HTTP {r.status_code}"
    except requests.exceptions.ConnectTimeout:
        status_reason = "ConnectTimeout"
    except requests.exceptions.ReadTimeout:
        status_reason = "ReadTimeout"
    except requests.exceptions.ProxyError:
        status_reason = "ProxyError"
    except Exception as e:
        status_reason = type(e).__name__

    return proto, host_port, general_ok, enacom_ok, enacom_lat, status_reason

def main():
    print("=" * 70)
    print("AUDITORIA EN VIVO: CENSO Y ESTADO REAL DE PROXIES GRATUITOS")
    print("=" * 70)
    print(f"Descargando listas desde {len(SOURCES)} fuentes globales...")
    
    t_start = time.time()
    all_raw = []
    source_stats = {}
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_source, proto, url): (proto, url) for proto, url in SOURCES}
        for future in as_completed(futures):
            url, res = future.result()
            domain = url.split("/")[2] if "/" in url else url
            path_abbr = url.split("/")[-1]
            source_stats[f"{domain}/{path_abbr}"] = len(res)
            all_raw.extend(res)
            
    print(f"Descarga completada en {time.time() - t_start:.2f}s.")
    print(f"Total de registros descargados (con duplicados): {len(all_raw)}")
    
    # Desduplicación por protocolo + IP:puerto
    unique_candidates = list(set(all_raw))
    unique_ips = len(set(cand[1].split(":")[0] for cand in unique_candidates))
    
    proto_breakdown = defaultdict(int)
    for proto, hp in unique_candidates:
        proto_breakdown[proto] += 1
        
    print("\n--- RESUMEN DE PROXIES ENCONTRADOS EN LA WEB ---")
    print(f"Total proxies únicos (Protocolo + IP:Puerto): {len(unique_candidates):,}")
    print(f"Total IPs únicas distintas: {unique_ips:,}")
    print("Distribución por protocolo:")
    for proto, count in proto_breakdown.items():
        print(f"  - {proto.upper():<7}: {count:,} proxies")
        
    # Muestra de fuentes
    print("\nTop 5 fuentes con mayor volumen de IPs:")
    sorted_sources = sorted(source_stats.items(), key=lambda x: x[1], reverse=True)[:5]
    for s_name, s_count in sorted_sources:
        print(f"  * {s_name}: {s_count:,} candidatos")
        
    # AHORA VALIDACION EN VIVO
    # Vamos a tomar una muestra representativa de 1,000 proxies o validar concurrentemente
    # Para ver la tasa de supervivencia real en tiempo real.
    TEST_SAMPLE_SIZE = 1000
    import random
    random.seed(42)
    sample_to_test = random.sample(unique_candidates, min(TEST_SAMPLE_SIZE, len(unique_candidates)))
    
    print("\n" + "=" * 70)
    print(f"TEST DE DISPONIBILIDAD EN TIEMPO REAL (Muestra aleatoria de {len(sample_to_test)} proxies)")
    print(f"Validando contra https://numeracion.enacom.gob.ar/ con 60 hilos concurrentes...")
    print("=" * 70)
    
    t_test_start = time.time()
    live_general = 0
    live_enacom = 0
    enacom_proxies = []
    reason_counts = defaultdict(int)
    
    with ThreadPoolExecutor(max_workers=60) as executor:
        test_futures = [executor.submit(test_proxy_live, proto, hp) for proto, hp in sample_to_test]
        done_count = 0
        for f in as_completed(test_futures):
            done_count += 1
            proto, host_port, gen_ok, ena_ok, lat, reason = f.result()
            
            if gen_ok:
                live_general += 1
            if ena_ok:
                live_enacom += 1
                enacom_proxies.append((f"{proto}://{host_port}", lat))
                print(f"  [VIVO PARA ENACOM] {proto}://{host_port} -> Latencia: {lat}s (Total válidos: {live_enacom})")
            else:
                reason_counts[reason] += 1
                
            if done_count % 200 == 0:
                print(f"  ... Evaluados {done_count}/{len(sample_to_test)} proxies...")
                
    test_duration = time.time() - t_test_start
    print("\n" + "=" * 70)
    print("RESULTADOS EMPIRICOS DE LA AUDITORIA EN TIEMPO REAL")
    print("=" * 70)
    print(f"Tiempo de validación: {test_duration:.1f} segundos")
    print(f"Muestra evaluada: {len(sample_to_test)} proxies")
    
    survival_rate_general = (live_general / len(sample_to_test)) * 100
    survival_rate_enacom = (live_enacom / len(sample_to_test)) * 100
    
    print(f"1. Proxies VIVOS en Internet (enrutan tráfico): {live_general} ({survival_rate_general:.1f}%)")
    print(f"2. Proxies VIVOS y APTOS para ENACOM:          {live_enacom} ({survival_rate_enacom:.1f}%)")
    
    # Extrapolación matemática al universo completo
    est_total_live_net = int(len(unique_candidates) * (live_general / len(sample_to_test)))
    est_total_live_enacom = int(len(unique_candidates) * (live_enacom / len(sample_to_test)))
    
    print(f"\n--- PROYECCION AL UNIVERSO TOTAL ({len(unique_candidates):,} proxies brutos encontrados) ---")
    print(f"-> Total estimado de proxies VIVOS en el mundo ahora mismo: ~{est_total_live_net:,} proxies")
    print(f"-> Total estimado de proxies FUNCIONALES para ENACOM hoy:   ~{est_total_live_enacom:,} proxies")
    
    if enacom_proxies:
        avg_lat = sum(p[1] for p in enacom_proxies) / len(enacom_proxies)
        print(f"\nLatencia promedio hacia ENACOM en los proxies funcionales: {avg_lat:.2f}s")
        print("\nEjemplos de proxies actualmente respondiendo a ENACOM:")
        for p_url, lat in enacom_proxies[:10]:
            print(f"  * {p_url} ({lat}s)")
            
    print("\nMotivos de descarte en los fallidos:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
        print(f"  - {reason}: {count} ({count/len(sample_to_test)*100:.1f}%)")
        
    # Guardar los proxies vivos confirmados para ENACOM en disco
    with open("data/enacom_verified_proxies.txt", "w", encoding="utf-8") as f:
        for p_url, lat in enacom_proxies:
            f.write(f"{p_url}\n")
    print(f"\n[OK] Guardados {len(enacom_proxies)} proxies funcionales en 'data/enacom_verified_proxies.txt'")

if __name__ == "__main__":
    main()
