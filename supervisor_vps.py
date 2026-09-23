#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SUPERVISOR INDUSTRIAL 24/7 (ARQUITECTURA HEXAGONAL) - VPS CENTRAL
Controlador maestro autónomo, tolerante a fallas y auto-regenerativo.

Características de Arquitectura Industrial:
1. Orquestación Hexagonal (Puertos y Adaptadores):
   Desacoplamiento total entre base de datos, scrapers y orquestación de lotes.
2. Supervisor Inmortal con Auto-Spawn:
   Mantiene siempre N workers activos en paralelo.
3. Rotación Preventiva Anti-Leak (Worker Lifecycle):
   Cada worker se retira ordenadamente tras 350 consultas para liberar recursos.
4. Circuit Breaker de Red y VPN:
   Monitorea la disponibilidad del servicio remoto cada 25 segundos.
5. Watchdog Sweeper de Huérfanos:
   Hilo centinela que libera registros colgados en 'procesando' con más de 15 minutos.
6. Tablero de Métricas en Tiempo Real (IPC Drainer):
   Métricas de rendimiento (RPM, latencia promedio, coincidencias y errores).
"""

import sys
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Configuración de codificación UTF-8 en Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.supervisor import SupervisorIndustrial
from adapters.scrapers.registry import ScraperRegistry
import config

# Configuración de Logging Industrial con Rotación Estricta
LOG_FILE = PROJECT_ROOT / "supervisor_247.log"
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=20 * 1024 * 1024,  # 20 MB
    backupCount=5,              # 5 copias = máx 100 MB en disco
    encoding="utf-8"
)
stream_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logging.getLogger("mysql.connector").setLevel(logging.WARNING)
logger = logging.getLogger("SupervisorVPS")


def main():
    parser = argparse.ArgumentParser(description="Supervisor Industrial 24/7 Multi-Scraper VPS")
    parser.add_argument("--scraper", type=str, default=None, help="Scraper a ejecutar (ej: iris, iris_http, iris_browser, datuar, claro, movistar, personal). Por defecto se deduce de --engine.")
    parser.add_argument("--engine", choices=["browser", "http"], default="http", help="Motor de extracción para IRIS: 'http' (HTTP Puro Ultrarrápido, default: http) o 'browser' (Chromium Playwright)")
    parser.add_argument("--prioridad", choices=["auto", "1", "2", "3"], default="auto", help="Estrategia de priorización: 'auto' (cascada P1 -> P2 -> P3), '1' (Solo 11 y Mendoza), '2' (Solo Sur), '3' (Solo Resto)")
    parser.add_argument("--workers", type=int, default=9, help="Cantidad de workers concurrentes (default: 9)")
    parser.add_argument("--batch-size", type=int, default=12, help="Tamaño de lote por reclamo en VPS (default: 12)")
    parser.add_argument("--max-queries-worker", type=int, default=350, help="Consultas máximas por worker antes de rotación preventiva (default: 350)")
    parser.add_argument("--delay-min", type=float, default=1.5, help="Pausa mínima de cortesía entre lotes (default: 1.5s)")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Pausa máxima de cortesía entre lotes (default: 2.5s)")
    parser.add_argument("--visible", action="store_true", help="Forzar navegadores visibles (por defecto headless si usa browser)")
    parser.add_argument("--forzar-horario", action="store_true", help="Ignorar restricción de horario comercial oficial (solo pruebas y excepciones)")
    parser.add_argument("--tor", action="store_true", help="Habilitar enrutamiento anónimo con Tor Stream Isolation (para scrapers Datuar, Claro, Movistar y Personal)")
    parser.add_argument("--proxy-pool", action="store_true", help="Habilitar pool de proxies públicos rotativos de alta velocidad (para scrapers Claro, Movistar y Personal)")
    parser.add_argument("--solo-sin-coincidencia", action="store_true", default=False, help="Filtrar solo registros que no tengan coincidencia en ninguna de las 3 compañías (Claro, Personal, Movistar)")
    parser.add_argument("--queue", choices=["registro_no_llame", "cola_automatizacion"], default=getattr(config, "QUEUE_TYPE", "registro_no_llame"), help="Origen de cola: 'registro_no_llame' o 'cola_automatizacion' (default: según config/env)")
    parser.add_argument("--auto-id", type=str, default=getattr(config, "COLA_AUTO_ID", "iris_scraper"), help="Identificador auto_id para 'cola_automatizacion' (default: iris_scraper)")
    parser.add_argument("--pc-id", type=str, default=getattr(config, "WORKER_PC_ID", "PC-00"), help="Identificador del nodo worker para 'cola_automatizacion' (default: PC-00)")

    args = parser.parse_args()

    # Determinar el alias de scraper según los argumentos
    if args.scraper:
        scraper_name = args.scraper.lower()
    else:
        scraper_name = "iris_http" if args.engine == "http" else "iris_browser"

    prio_val = None if args.prioridad == "auto" else int(args.prioridad)

    scraper_kwargs = {}
    if "browser" in scraper_name:
        scraper_kwargs["headless"] = not args.visible
    if args.tor:
        scraper_kwargs["use_tor"] = True
    if args.proxy_pool:
        scraper_kwargs["use_proxy_pool"] = True

    supervisor = SupervisorIndustrial(
        scraper_name=scraper_name,
        workers=args.workers,
        batch_size=args.batch_size,
        max_queries_worker=args.max_queries_worker,
        prioridad=prio_val,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        scraper_kwargs=scraper_kwargs,
        forzar_horario=args.forzar_horario,
        solo_sin_coincidencia=args.solo_sin_coincidencia,
        queue_type=args.queue,
        auto_id=args.auto_id,
        pc_id=args.pc_id
    )

    supervisor.ejecutar()


if __name__ == "__main__":
    main()
