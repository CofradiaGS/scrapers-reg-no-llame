# -*- coding: utf-8 -*-
"""
EXPERIMENTO DE ALTA CONCURRENCIA: 15 WORKERS SIMULTÁNEOS DESDE LA MISMA IP
Objetivo:
1. Evaluar si el servidor de ENACOM (CheckPoint Firewall + OpenResty + IIS) tolera
   15 conexiones TCP/HTTP simultáneas desde la misma dirección IP sin dropear paquetes.
2. Comparar el consumo de recursos y la latencia con:
   - 15 Workers HTTP puros (Requests/urllib3 con sesiones aisladas).
   - 15 Contextos de Navegación concurrentes (Playwright async con sesiones aisladas).
3. Determinar el punto óptimo de saturación vs rendimiento antes de que el servidor degrade.
"""

import sys
import os
import time
import asyncio
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

urllib3.disable_warnings()

TARGET_URL = "https://numeracion.enacom.gob.ar/Numeracion.aspx"
CAPTCHA_URL = "https://numeracion.enacom.gob.ar/Captcha.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

NUM_WORKERS = 15

# =====================================================================
# FASE 1: 15 WORKERS HTTP SIMULTÁNEOS (NIVEL DE RED)
# =====================================================================
def worker_http(worker_id: int):
    session = requests.Session()
    session.headers.update(HEADERS)
    t0 = time.time()
    res = {
        "id": worker_id,
        "page_status": None,
        "captcha_status": None,
        "page_bytes": 0,
        "duration": 0.0,
        "error": None
    }
    try:
        # Petición a la portada para crear sesión ASP.NET
        r_page = session.get(TARGET_URL, timeout=12.0, verify=False)
        res["page_status"] = r_page.status_code
        res["page_bytes"] = len(r_page.text)
        
        # Descarga de captcha de esa sesión
        r_cap = session.get(CAPTCHA_URL, timeout=12.0, verify=False)
        res["captcha_status"] = r_cap.status_code
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:40]}"
        
    res["duration"] = round(time.time() - t0, 2)
    return res

def test_15_http_workers():
    print("\n" + "=" * 75)
    print(f"FASE 1: LANZANDO {NUM_WORKERS} WORKERS HTTP SIMULTÁNEOS (MISMA IP)")
    print("=" * 75)
    
    t_start = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = [executor.submit(worker_http, i) for i in range(1, NUM_WORKERS + 1)]
        for f in as_completed(futures):
            results.append(f.result())
            
    total_time = round(time.time() - t_start, 2)
    results = sorted(results, key=lambda x: x["id"])
    
    print(f"{'Worker':<10} | {'Status Portada':<16} | {'Status Captcha':<16} | {'Tiempo':<10} | {'Error'}")
    print("-" * 75)
    exitosos = 0
    tiempos = []
    for r in results:
        err = r["error"] or "Ninguno"
        print(f"Worker #{r['id']:<3} | HTTP {r['page_status']} ({r['page_bytes']}b)  | HTTP {r['captcha_status']}        | {r['duration']}s     | {err}")
        if r["page_status"] == 200 and r["captcha_status"] == 200:
            exitosos += 1
            tiempos.append(r["duration"])
            
    avg_t = round(sum(tiempos) / len(tiempos), 2) if tiempos else 0
    print("-" * 75)
    print(f"Resultado Fase 1:")
    print(f"- Exitosos: {exitosos} / {NUM_WORKERS} ({round(exitosos/NUM_WORKERS*100, 1)}%)")
    print(f"- Tiempo total de la ráfaga: {total_time}s")
    print(f"- Latencia promedio por worker: {avg_t}s")
    return exitosos

# =====================================================================
# FASE 2: 15 CONTEXTOS DE NAVEGACIÓN SIMULTÁNEOS (PLAYWRIGHT ASYNC)
# =====================================================================
async def worker_pw_context(browser, worker_id: int):
    t0 = time.time()
    res = {
        "id": worker_id,
        "ok": False,
        "duration": 0.0,
        "detail": ""
    }
    try:
        # Cada worker tiene su propio BrowserContext con cookies y almacenamiento aislados
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=HEADERS["User-Agent"]
        )
        page = await context.new_page()
        
        # Bloqueo de fuentes y CSS innecesarios para optimizar memoria
        async def intercept(route):
            url = route.request.url.lower()
            if any(ext in url for ext in [".woff", ".woff2", ".ttf", "font-awesome", "favicon.ico"]):
                await route.abort()
            else:
                await route.continue_()
                
        await page.route("**/*", intercept)
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=15000)
        
        img = page.locator("#imgCaptcha")
        count = await img.count()
        visible = await img.is_visible() if count > 0 else False
        
        if visible:
            res["ok"] = True
            res["detail"] = "Formulario + Captcha listos"
        else:
            res["detail"] = "Captcha no visible"
            
        await context.close()
    except Exception as e:
        res["detail"] = f"{type(e).__name__}: {str(e)[:35]}"
        
    res["duration"] = round(time.time() - t0, 2)
    return res

async def test_15_playwright_contexts():
    from playwright.async_api import async_playwright
    print("\n" + "=" * 75)
    print(f"FASE 2: LANZANDO {NUM_WORKERS} CONTEXTOS PLAYWRIGHT EN PARALELO (MISMA IP)")
    print("Arquitectura: 1 Motor Chromium con 15 BrowserContexts independientes")
    print("=" * 75)
    
    t_start = time.time()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        
        tasks = [worker_pw_context(browser, i) for i in range(1, NUM_WORKERS + 1)]
        results = await asyncio.gather(*tasks)
        await browser.close()
        
    total_time = round(time.time() - t_start, 2)
    results = sorted(results, key=lambda x: x["id"])
    
    print(f"{'Contexto PW':<14} | {'Éxito':<8} | {'Tiempo':<10} | {'Detalle'}")
    print("-" * 75)
    exitosos = 0
    tiempos = []
    for r in results:
        print(f"Contexto #{r['id']:<4} | {str(r['ok']):<8} | {r['duration']}s     | {r['detail']}")
        if r["ok"]:
            exitosos += 1
            tiempos.append(r["duration"])
            
    avg_t = round(sum(tiempos) / len(tiempos), 2) if tiempos else 0
    print("-" * 75)
    print(f"Resultado Fase 2:")
    print(f"- Exitosos: {exitosos} / {NUM_WORKERS} ({round(exitosos/NUM_WORKERS*100, 1)}%)")
    print(f"- Tiempo total de la ráfaga: {total_time}s")
    print(f"- Latencia promedio por contexto: {avg_t}s")
    return exitosos

def main():
    print("=" * 75)
    print("BENCHMARK DE ESTRÉS: 15 WORKERS SIMULTÁNEOS DESDE LA MISMA IP")
    print("Servidor Destino: https://numeracion.enacom.gob.ar/")
    print("=" * 75)
    
    # 1. Probar nivel HTTP
    exitosos_http = test_15_http_workers()
    
    # Pausa de cortesía de 3 segundos
    time.sleep(3)
    
    # 2. Probar nivel Navegador Playwright
    exitosos_pw = asyncio.run(test_15_playwright_contexts())
    
    # Veredicto
    print("\n" + "=" * 75)
    print("CONCLUSIONES DEL EXPERIMENTO CON 15 WORKERS")
    print("=" * 75)
    print(f"1. Conexiones HTTP simultáneas exitosas: {exitosos_http}/{NUM_WORKERS}")
    print(f"2. Contextos de Navegador simultáneos exitosos: {exitosos_pw}/{NUM_WORKERS}")
    
    if exitosos_http == NUM_WORKERS and exitosos_pw == NUM_WORKERS:
        print("[VEREDICTO]: TOLERANCIA TOTAL. ENACOM soporta 15 workers simultáneos por IP sin degradar.")
    elif exitosos_http >= 10:
        print("[VEREDICTO]: TOLERANCIA ALTA. ENACOM acepta alta concurrencia pero con cierta dispersión de latencia.")
    else:
        print("[VEREDICTO]: SATURACIÓN. Con 15 workers se alcanzan límites de cola o conexión en el servidor/proxy.")

if __name__ == "__main__":
    main()
