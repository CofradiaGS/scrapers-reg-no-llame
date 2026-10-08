# -*- coding: utf-8 -*-
"""
Prueba de Extracción de Portabilidad Concurrente sobre la misma IP
Lanza 2 adapters EnacomWebAdapter en paralelo para comprobar que:
1. Resuelven captchas concurrentemente sin interferencia de sesión.
2. Extraen prestador original y prestador actual al mismo tiempo.
3. Miden la aceleración total del throughput.
"""
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.domain.entities import Linea
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter

def consultar_worker(worker_id: int, ani: str):
    t0 = time.time()
    adapter = EnacomWebAdapter(headless=True)
    try:
        adapter.iniciar()
        linea = Linea(ani=ani)
        resultado = adapter.consultar_linea(linea)
        dt = round(time.time() - t0, 2)
        return worker_id, ani, resultado.status.value, resultado.datos_extra, dt, None
    except Exception as e:
        return worker_id, ani, "ERROR", {}, round(time.time() - t0, 2), str(e)
    finally:
        adapter.cerrar()

def main():
    print("=" * 70)
    print("TEST DE SCRAPING CONCURRENTE REAL (MISMA IP) CON ENACOM WEB")
    print("=" * 70)

    # Dos números reales de prueba
    numeros = ["1144445555", "1155556666"]
    print(f"Lanzando {len(numeros)} consultas simultáneas en 2 navegadores paralelos...")

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=len(numeros)) as executor:
        futures = [executor.submit(consultar_worker, i+1, num) for i, num in enumerate(numeros)]
        resultados = [f.result() for f in futures]

    total_time = round(time.time() - t_start, 2)
    print("-" * 70)
    for wid, ani, status, datos, dt, err in resultados:
        prestador = datos.get("prestador_actual") or datos.get("prestador_original") or "N/D"
        print(f"Worker #{wid} | ANI: {ani} | Status: {status} | Prestador: {prestador} | Tiempo: {dt}s | Error: {err}")
    print("-" * 70)
    print(f"Tiempo total transcurrido: {total_time}s para {len(numeros)} consultas completas en paralelo.")
    print("=" * 70)

if __name__ == "__main__":
    main()
