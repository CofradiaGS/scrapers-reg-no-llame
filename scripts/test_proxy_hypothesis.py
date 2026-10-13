# -*- coding: utf-8 -*-
"""
EXPERIMENTO CIENTIFICO: Demostración empírica de las 2 hipótesis
Hipótesis 1: ¿Los proxies estaban vivos pero fallaron porque los probamos todos juntos (concurrencia / throttling de ENACOM)?
Hipótesis 2: ¿Los proxies son intrínsecamente inestables / caídos en internet, independientemente de ENACOM?

Metodología:
1. Control Neutro: Se prueba cada proxy argentino contra un servidor neutro internacional (https://api.ipify.org / http://ip-api.com).
   - Si no responde al control neutro -> El proxy está CAIDO en internet (no es culpa de ENACOM).
   - Si responde al control neutro pero no a ENACOM -> ENACOM lo bloquea / no soporta CONNECT 443.
2. Prueba Secuencial Estricta: Cero concurrencia (1 solo hilo), con pausa de 1.5 segundos entre cada uno
   para eliminar al 100% cualquier posibilidad de saturación o anti-DDoS de ENACOM.
3. Test de Estabilidad Temporal: Se prueba el proxy que funcionó antes en intervalos de 10 segundos
   para medir su tasa de estabilidad y caída.
"""

import sys
import time
import requests
import urllib3

urllib3.disable_warnings()

# Lista de proxies argentinos cosechados
AR_PROXIES = [
    ("http", "181.192.2.23:8080"),
    ("http", "190.229.64.252:443"),
    ("socks5", "190.229.64.252:443"),
    ("http", "201.190.178.191:8080"),
    ("http", "198.12.37.18:8080"),
    ("http", "190.7.19.110:8080"),
    ("http", "131.0.235.26:55555"),
    ("https", "181.209.114.220:8080"),
    ("http", "45.229.17.74:999"),
    ("http", "181.79.57.57:8080"),
    ("http", "200.110.218.10:999"),
    ("http", "190.229.164.58:9080"),
    ("http", "181.209.125.237:999"),
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
    ("socks4", "198.12.37.25:1080"),
    ("socks4", "200.63.95.17:1085"),
    ("socks4", "200.43.43.124:4145"),
    ("socks4", "190.228.33.45:1085"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def main():
    print("=" * 75, flush=True)
    print("EXPERIMENTO DE DISCRIMINACION: ENACOM CONCURRENTE vs PROXY INESTABLE", flush=True)
    print("=" * 75, flush=True)
    
    # 0. Verificar primero salud de ENACOM desde IP directa (Línea Base)
    t0_base = time.time()
    try:
        r_base = requests.get("https://numeracion.enacom.gob.ar/Numeracion.aspx", verify=False, timeout=5)
        print(f"[LINEA BASE] ENACOM responde en directo: {r_base.status_code} ({round(time.time() - t0_base, 2)}s)", flush=True)
    except Exception as e:
        print(f"[LINEA BASE] ENACOM caido en directo: {e}", flush=True)
        
    print("\nIniciando test SECUENCIAL (1 por 1, sin concurrencia, timeout de 8s)...", flush=True)
    print(f"{'PROXY':<30} | {'INTERNET (Neutro)':<20} | {'ENACOM (HTTPS 443)':<20}", flush=True)
    print("-" * 75, flush=True)
    
    vivos_neutro = 0
    vivos_enacom = 0
    
    for proto, hp in AR_PROXIES:
        actual_proto = "socks5h" if proto == "socks5" else proto
        p_url = f"{actual_proto}://{hp}"
        proxies = {"http": p_url, "https": p_url}
        
        # Test 1: Servidor Neutro (HTTP / HTTPS público estándar)
        status_neutro = "CAIDO"
        t0 = time.time()
        try:
            r_neutro = requests.get("http://ip-api.com/json", proxies=proxies, timeout=6.0)
            if r_neutro.status_code == 200:
                status_neutro = f"VIVO ({round(time.time() - t0, 1)}s)"
                vivos_neutro += 1
            else:
                status_neutro = f"HTTP {r_neutro.status_code}"
        except requests.exceptions.ProxyError:
            status_neutro = "ProxyError"
        except requests.exceptions.ConnectTimeout:
            status_neutro = "ConnectTimeout"
        except requests.exceptions.ConnectionError:
            status_neutro = "ConnRefused"
        except Exception as e:
            status_neutro = type(e).__name__[:12]
            
        # Test 2: ENACOM (Secuencial estricto, sin hilos simultáneos)
        status_enacom = "NO TEST"
        if "VIVO" in status_neutro:
            t1 = time.time()
            try:
                r_ena = requests.get(
                    "https://numeracion.enacom.gob.ar/Numeracion.aspx",
                    headers=HEADERS,
                    proxies=proxies,
                    timeout=8.0,
                    verify=False
                )
                lat_ena = round(time.time() - t1, 1)
                if r_ena.status_code == 200 and ("ENACOM" in r_ena.text or "Numeración" in r_ena.text or "ctl00" in r_ena.text):
                    status_enacom = f"VIVO ({lat_ena}s)"
                    vivos_enacom += 1
                elif r_ena.status_code == 303 or "firewall" in r_ena.text:
                    status_enacom = "Firewall 303"
                else:
                    status_enacom = f"HTTP {r_ena.status_code}"
            except requests.exceptions.ProxyError:
                status_enacom = "No-CONNECT-443"
            except requests.exceptions.ConnectTimeout:
                status_enacom = "Timeout-443"
            except requests.exceptions.ConnectionError:
                status_enacom = "Reset-by-peer"
            except Exception as e:
                status_enacom = type(e).__name__[:12]
        else:
            status_enacom = "N/A (Muerto en origen)"
            
        print(f"{p_url:<30} | {status_neutro:<20} | {status_enacom:<20}", flush=True)
        time.sleep(1.0) # Pausa cortés para garantizar cero concurrencia
        
    print("\n" + "=" * 75, flush=True)
    print("PARTE 2: TEST DE RESISTENCIA TEMPORAL (Proxy 181.192.2.23:8080)", flush=True)
    print("Midiendo comportamiento cada 5 segundos durante 1 minuto...", flush=True)
    print("=" * 75, flush=True)
    
    target_p = "http://181.192.2.23:8080"
    target_proxies = {"http": target_p, "https": target_p}
    
    intentos_ok = 0
    total_intentos = 8
    for i in range(1, total_intentos + 1):
        t_sub = time.time()
        try:
            r = requests.get(
                "https://numeracion.enacom.gob.ar/Numeracion.aspx",
                headers=HEADERS,
                proxies=target_proxies,
                timeout=6.0,
                verify=False
            )
            lat = round(time.time() - t_sub, 2)
            if r.status_code == 200 and ("ENACOM" in r.text or "Numeración" in r.text):
                print(f"  Intento #{i}: [EXITO] Respondió en {lat}s", flush=True)
                intentos_ok += 1
            else:
                print(f"  Intento #{i}: [FALLO] Status {r.status_code} ({lat}s)", flush=True)
        except Exception as e:
            print(f"  Intento #{i}: [CAIDO] {type(e).__name__} tras {round(time.time() - t_sub, 2)}s", flush=True)
        time.sleep(5.0)
        
    print("\n" + "=" * 75, flush=True)
    print("CONCLUSION CIENTIFICA FINAL", flush=True)
    print("=" * 75, flush=True)
    print(f"Total proxies evaluados 1 por 1: {len(AR_PROXIES)}")
    print(f"Proxies vivos en internet neutro: {vivos_neutro}")
    print(f"Proxies funcionales en ENACOM:   {vivos_enacom}")
    print(f"Estabilidad temporal del proxy funcional: {intentos_ok}/{total_intentos} éxitos ({(intentos_ok/total_intentos)*100:.1f}%)")

if __name__ == "__main__":
    main()
