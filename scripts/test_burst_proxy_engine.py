# -*- coding: utf-8 -*-
"""
Test del Motor Reactivo de Ráfaga 'Burn-till-Dead' para ENACOM
Ejecuta el harvester en segundo plano, caza proxies argentinos/elite vivos
y los exprime al máximo sin techo fijo de consultas hasta que mueran o alcancen el límite diario.
"""

import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestBurstEngine")

from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter
from adapters.network.burst_proxy_manager import BurstProxyManager

# Lista de prueba con números para consulta
NUMEROS_PRUEBA = [
    "1155986877", "1165432190", "1144332211", "1133221144",
    "1122334455", "1166778899", "1155667788", "1144556677",
    "1133445566", "1122113344", "1177889900", "1188990011",
    "1199001122", "1144112233", "1155223344", "1166334455"
]

def main():
    print("=" * 70)
    print("INICIANDO MOTOR DE RAFAGA REACTIVA 'BURN-TILL-DEAD'")
    print("Objetivo: Exprimir cada proxy hasta su muerte natural o límite diario")
    print("=" * 70)

    # 1. Iniciar Harvester de fondo
    burst_mgr = BurstProxyManager(check_interval=60, probe_timeout=3.5)
    burst_mgr.start()
    
    print("Harvester iniciado. Buscando proxies en caliente...")
    print("Esperando primer proxy vivo validado contra ENACOM (máx 30s)...")
    
    # 2. Inicializar Adapter de Playwright
    adapter = EnacomWebAdapter(headless=True)
    adapter.iniciar()
    
    active_proxy = None
    queries_on_current_proxy = 0
    total_successful_queries = 0
    num_idx = 0
    
    try:
        # Bucle de procesamiento por ráfaga
        while num_idx < len(NUMEROS_PRUEBA):
            # Si no tenemos proxy activo, obtener uno de la cola
            if not active_proxy:
                print("\n[BUSCANDO PROXY] Esperando proxy fresco de la cola del Harvester...")
                active_proxy = burst_mgr.get_next_proxy(timeout=35.0)
                if not active_proxy:
                    print("No hay proxies disponibles en este momento. El harvester sigue buscando...")
                    time.sleep(5)
                    continue
                    
                print(f"\n>> [NUEVO PROXY ASIGNADO] {active_proxy} -> Rotando contexto Playwright en caliente...")
                queries_on_current_proxy = 0
                ok = adapter.rotar_proxy(active_proxy)
                if not ok:
                    print(f"[-] Falló al navegar la portada con {active_proxy}. Descartando de inmediato.")
                    burst_mgr.report_proxy_result(active_proxy, success=False, reason="Fallo al iniciar")
                    active_proxy = None
                    continue
                print(f"[+] Contexto listo con proxy {active_proxy}. Iniciando ráfaga sin techo...")

            ani = NUMEROS_PRUEBA[num_idx]
            linea = Linea(ani=ani)
            print(f"\n[Consulta #{num_idx+1}] Línea: {ani} | Proxy: {active_proxy} (Acumuladas en este proxy: {queries_on_current_proxy})")
            
            t0 = time.time()
            res = adapter.consultar_linea(linea)
            dt = round(time.time() - t0, 2)
            
            # Caso 1: Éxito o Portabilidad
            if res.status in (StatusScraping.COINCIDENCIA, StatusScraping.SIN_COINCIDENCIA):
                queries_on_current_proxy += 1
                total_successful_queries += 1
                burst_mgr.report_proxy_result(active_proxy, success=True)
                print(f"  -> [OK en {dt}s] Status: {res.status.value} | {res.descripcion[:60]}")
                num_idx += 1
                
            # Caso 2: Alcanzó el límite diario de 100 consultas de ENACOM
            elif "diarias" in res.descripcion.lower() or "límite" in res.descripcion.lower():
                print(f"  -> [CUOTA ALCANZADA] ENACOM reportó límite diario en {active_proxy} tras {queries_on_current_proxy} consultas.")
                burst_mgr.report_proxy_result(active_proxy, success=False, reason="Limite 100 consultas")
                active_proxy = None # Forzar rotación
                
            # Caso 3: El proxy murió (timeout, caída de socket, error de conexión)
            else:
                print(f"  -> [PROXY FALLECIDO] Error en consulta tras {dt}s: '{res.descripcion}'.")
                print(f"  -> Rindió {queries_on_current_proxy} consultas útiles antes de morir.")
                burst_mgr.report_proxy_result(active_proxy, success=False, reason="Socket caido/timeout")
                active_proxy = None # Forzar rotación al instante
                # No incrementamos num_idx para reintentar la misma línea con el nuevo proxy

    except KeyboardInterrupt:
        print("\nDetenido por usuario.")
    finally:
        adapter.cerrar()
        burst_mgr.stop()
        
    print("\n" + "=" * 70)
    print("RESUMEN DEL TEST DE RAFAGA REACTIVA")
    print("=" * 70)
    print(f"Total consultas ejecutadas con éxito: {total_successful_queries}")
    print(f"Total proxies agotados/descartados:   {burst_mgr.stats['proxies_exhausted']}")
    print(f"Total proxies cosechados por Harvester: {burst_mgr.stats['total_harvested']}")

if __name__ == "__main__":
    main()
