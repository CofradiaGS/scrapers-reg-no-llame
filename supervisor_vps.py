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
import multiprocessing
from pathlib import Path

# Configuración de codificación UTF-8 en Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# Garantizar que los procesos hijos de multiprocessing usen el mismo intérprete venv
if hasattr(multiprocessing, "set_executable"):
    multiprocessing.set_executable(sys.executable)

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.supervisor import SupervisorIndustrial
from adapters.scrapers.registry import ScraperRegistry
import config

LOG_FILE = PROJECT_ROOT / "supervisor_247.log"
stream_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
stream_handler.setFormatter(formatter)

# En procesos worker (hijos spawn), usar únicamente salida por consola para evitar bloqueos de archivo en Windows
handlers = [stream_handler]
if __name__ == "__main__":
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler
        file_handler = ConcurrentRotatingFileHandler(
            LOG_FILE,
            maxBytes=20 * 1024 * 1024,  # 20 MB
            backupCount=5,              # 5 copias = máx 100 MB en disco
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except ImportError:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=20 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

logging.basicConfig(
    level=logging.INFO,
    handlers=handlers,
    force=True
)
logging.getLogger("mysql.connector").setLevel(logging.WARNING)
logging.getLogger("stem").setLevel(logging.WARNING)
logging.getLogger("stem.control").setLevel(logging.ERROR)
logger = logging.getLogger("SupervisorVPS")


def main():
    parser = argparse.ArgumentParser(description="Supervisor Industrial 24/7 Multi-Scraper VPS")
    parser.add_argument("--scraper", type=str, default=None, help="Scraper a ejecutar (ej: iris, iris_http, iris_browser, datuar, claro, movistar, personal). Por defecto se deduce de --engine.")
    parser.add_argument("--engine", choices=["browser", "http"], default="http", help="Motor de extracción para IRIS: 'http' (HTTP Puro Ultrarrápido, default: http) o 'browser' (Chromium Playwright)")
    parser.add_argument("--prioridad", choices=["auto", "1", "2", "3"], default="auto", help="Estrategia de priorización: 'auto' (cascada P1 -> P2 -> P3), '1' (Solo 11 y Mendoza), '2' (Solo Sur), '3' (Solo Resto)")
    parser.add_argument("--workers", type=int, default=9, help="Cantidad de workers concurrentes (default: 9)")
    parser.add_argument("--batch-size", type=int, default=50, help="Tamaño de lote por reclamo en VPS (default: 50)")
    parser.add_argument("--buffer-size", type=int, default=getattr(config, "BUFFER_FLUSH_SIZE", 500), help="Cantidad de registros acumulados en RAM antes de flush atómico (default: 500)")
    parser.add_argument("--buffer-timeout", type=float, default=getattr(config, "BUFFER_MAX_DELAY", 300.0), help="Tiempo máximo en segundos en RAM antes de flush a MySQL (default: 300.0s = 5min)")
    parser.add_argument("--empty-queue-pause", type=float, default=getattr(config, "EMPTY_QUEUE_PAUSE_MAX_SEC", 900.0), help="Pausa máxima en segundos del Centinela cuando la cola está vacía (default: 900.0s = 15min)")
    parser.add_argument("--max-queries-worker", type=int, default=350, help="Consultas máximas por worker antes de rotación preventiva (default: 350)")
    parser.add_argument("--delay-min", type=float, default=1.5, help="Pausa mínima de cortesía entre lotes (default: 1.5s)")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Pausa máxima de cortesía entre lotes (default: 2.5s)")
    parser.add_argument("--visible", action="store_true", help="Forzar navegadores visibles (por defecto headless si usa browser)")
    parser.add_argument("--forzar-horario", action="store_true", default=False, help="Ignorar restricción de horario comercial oficial (solo pruebas y excepciones)")
    parser.add_argument("--tor", dest="tor", action="store_true", default=None, help="Habilitar enrutamiento anónimo con Tor Stream Isolation (para scrapers Datuar, Claro, Movistar y Personal)")
    parser.add_argument("--no-tor", dest="tor", action="store_false", help="Deshabilitar Tor y conectar de forma directa a alta velocidad")
    parser.add_argument("--proxy-pool", action="store_true", help="Habilitar pool de proxies públicos rotativos de alta velocidad (para scrapers Claro, Movistar y Personal)")
    parser.add_argument("--solo-sin-coincidencia", action="store_true", default=False, help="Filtrar solo registros que no tengan coincidencia en ninguna de las 3 compañías (Claro, Personal, Movistar)")
    parser.add_argument("--queue", choices=["registro_no_llame", "cola_automatizacion"], default=getattr(config, "QUEUE_TYPE", "registro_no_llame"), help="Origen de cola: 'registro_no_llame' o 'cola_automatizacion' (default: según config/env)")
    parser.add_argument("--auto-id", type=str, default=getattr(config, "COLA_AUTO_ID", "iris_scraper"), help="Identificador auto_id para 'cola_automatizacion' (default: iris_scraper)")
    parser.add_argument("--pc-id", type=str, default=getattr(config, "WORKER_PC_ID", "PC-00"), help="Identificador del nodo worker para 'cola_automatizacion' (default: PC-00)")
    parser.add_argument("--sync-now", action="store_true", help="Fuerza subida masiva nocturna inmediata (Push en chunks de a 5.000) de tareas terminadas al VPS y finaliza")
    parser.add_argument("--pull-now", action="store_true", help="Fuerza descarga masiva inmediata (Pull de 10.000 tareas) desde el VPS a SQLite local y finaliza")
    parser.add_argument("--force-push", action="store_true", default=False, help="Forzar subida continua al VPS en chunks de 5.000 con semáforo distribuido ignorando la restricción de ventana nocturna")
    parser.add_argument("--local-staging", dest="local_staging", action="store_true", default=None, help="Habilita persistencia local desacoplada SQLite WAL (default según config.LOCAL_STAGING_ENABLED)")
    parser.add_argument("--no-local-staging", dest="local_staging", action="store_false", help="Deshabilita persistencia local y conecta directo al VPS")
    parser.add_argument("--orphan-timeout", type=int, default=None, help="Tiempo en minutos de inactividad antes de considerar huérfano un registro en VPS (default: 720 = 12 horas)")

    args = parser.parse_args()

    # Comandos operativos directos de sincronización (CLI)
    if args.sync_now:
        print("🌙 [CLI SYNC-NOW] Iniciando subida forzada inmediata a MySQL VPS...")
        from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter
        from adapters.queue.vps_sync_adapter import VPSSyncAdapter
        from core.use_cases.sync_push_use_case import SincronizarPushNocturnoUseCase

        local_repo = SQLiteStagingAdapter(tipo_cola=args.queue, auto_id=args.auto_id, pc_id=args.pc_id)
        remote_repo = VPSSyncAdapter(pool_size=1)
        use_case = SincronizarPushNocturnoUseCase(remote_repo=remote_repo, local_repo=local_repo)
        res = use_case.ejecutar(tipo_cola=args.queue, chunk_size=getattr(config, "SYNC_CHUNK_SIZE", 5000))
        print(f"📊 Resumen de sincronización: {res}")
        return

    if args.pull_now:
        print("💧 [CLI PULL-NOW] Iniciando recarga forzada de 10.000 tareas desde MySQL VPS...")
        from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter
        from adapters.queue.vps_sync_adapter import VPSSyncAdapter
        from core.use_cases.sync_pull_use_case import SincronizarPullMatutinoUseCase

        local_repo = SQLiteStagingAdapter(tipo_cola=args.queue, auto_id=args.auto_id, pc_id=args.pc_id)
        remote_repo = VPSSyncAdapter(pool_size=1)
        use_case = SincronizarPullMatutinoUseCase(remote_repo=remote_repo, local_repo=local_repo)
        res = use_case.ejecutar(
            tipo_cola=args.queue,
            limit=getattr(config, "PULL_CHUNK_SIZE", 10000),
            forzar=True,
            auto_id=args.auto_id,
            pc_id=args.pc_id,
            scraper_actual=args.scraper or ("telcos" if args.queue == "cola_automatizacion" else "iris")
        )
        print(f"📊 Resumen de recarga: {res}")
        return

    # Determinar el alias de scraper según los argumentos
    if args.scraper:
        scraper_name = args.scraper.lower()
    else:
        scraper_name = "iris_http" if args.engine == "http" else "iris_browser"

    prio_val = None if args.prioridad == "auto" else int(args.prioridad)

    scraper_kwargs = {}
    if "browser" in scraper_name:
        scraper_kwargs["headless"] = not args.visible
    if args.tor is not None:
        scraper_kwargs["use_tor"] = args.tor
    elif getattr(config, "TOR_ENABLED", False):
        scraper_kwargs["use_tor"] = True
    if args.proxy_pool:
        scraper_kwargs["use_proxy_pool"] = True

    # Asignar automáticamente el usuario de IRIS correspondiente según la cola elegida
    if "iris" in scraper_name:
        if args.queue == "registro_no_llame":
            scraper_kwargs["user"] = getattr(config, "IRIS_REG_NO_LLAME_USER", "enorozco")
            scraper_kwargs["password"] = getattr(config, "IRIS_REG_NO_LLAME_PASS", "TucaChorron2026.")
        else:
            scraper_kwargs["user"] = getattr(config, "IRIS_COLA_AUTO_USER", "emartina")
            scraper_kwargs["password"] = getattr(config, "IRIS_COLA_AUTO_PASS", "Inicio-2026")

    # Inyectar proxy residencial exclusivo para ENACOM si está configurado en .env
    if "enacom" in scraper_name:
        enacom_proxy = getattr(config, "ENACOM_PROXY", None)
        if enacom_proxy:
            scraper_kwargs["proxy"] = enacom_proxy

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
        pc_id=args.pc_id,
        buffer_flush_size=args.buffer_size,
        buffer_max_delay=args.buffer_timeout,
        empty_queue_pause_max_sec=args.empty_queue_pause,
        use_local_staging=args.local_staging,
        force_push=args.force_push,
        minutos_inactividad_huerfanos=args.orphan_timeout
    )

    supervisor.ejecutar()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
