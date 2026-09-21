#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CLI Principal del Sistema de Scraping (Arquitectura Hexagonal)
Interfaz unificada para pruebas, monitoreo, estadísticas y ejecución industrial.
"""

import sys
import json
import argparse
import logging
from pathlib import Path

# Asegurar encoding UTF-8 en consola de Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from core.domain.entities import Linea
from core.domain.exceptions import FueraDeHorarioComercialException
from core.use_cases.process_batch_use_case import ProcesarLoteUseCase
from core.use_cases.cleanup_orphans_use_case import LiberarHuerfanosUseCase
from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
from adapters.queue.memory_adapter import MemoryQueueAdapter
from adapters.scrapers.registry import ScraperRegistry
from runtime.supervisor import SupervisorIndustrial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("mysql.connector").setLevel(logging.WARNING)
logger = logging.getLogger("MainCLI")


def cmd_stats(args):
    """Muestra estadísticas actuales de la cola central en VPS."""
    adapter = MySQLQueueAdapter(pool_size=2, pool_name="cli_stats_pool")
    stats = adapter.obtener_estadisticas()

    print("\n" + "=" * 55)
    print("📊 ESTADÍSTICAS GLOBALES DE LA COLA VPS")
    print("=" * 55)
    if not stats:
        print("No se encontraron registros en la tabla de cola.")
    else:
        # Agrupar por scraper
        print(f"{'Scraper / Etapa':<30} | {'Cantidad':>15}")
        print("-" * 55)
        for clave, cant in sorted(stats.items()):
            if "_" in clave:
                print(f"  • {clave:<28} | {cant:>15,}")
        print("-" * 55)
        print("RESUMEN POR ESTADO:")
        for est in ["pendiente", "procesando", "completado", "error"]:
            if est in stats:
                print(f"  • {est.upper():<28} | {stats[est]:>15,}")
    print("=" * 55 + "\n")


def cmd_list_scrapers(args):
    """Lista todos los motores de scraping disponibles en el sistema."""
    disponibles = ScraperRegistry.listar_disponibles()
    print("\n" + "=" * 50)
    print("🔌 SCRAPERS REGISTRADOS EN ARQUITECTURA HEXAGONAL")
    print("=" * 50)
    for s in disponibles:
        print(f"  • {s}")
    print("=" * 50 + "\n")


def cmd_test_line(args):
    """Prueba la consulta de una única línea telefónica."""
    ani = args.ani.strip()
    scraper_name = args.scraper.lower()
    print(f"\n🔍 Consultando línea {ani} con motor '{scraper_name}'...")

    try:
        scraper_kwargs = {"forzar_horario": args.forzar_horario}
        if getattr(args, "use_tor", None) is not None:
            scraper_kwargs["use_tor"] = args.use_tor
        if getattr(args, "use_proxy_pool", False):
            scraper_kwargs["use_proxy_pool"] = True
            scraper_kwargs["use_tor"] = False

        scraper = ScraperRegistry.obtener(scraper_name, **scraper_kwargs)
        scraper.iniciar()
        if not scraper.autenticar():
            print("❌ Error: No se pudo autenticar en el scraper.")
            return

        dni = getattr(args, "dni", None)
        res = scraper.consultar_linea(Linea(ani, dni=dni))
        scraper.cerrar()

        print("\n" + "=" * 55)
        print(f"🎯 RESULTADO NORMALIZADO: {ani}")
        print("=" * 55)
        print(f"  • Estatus:     {res.status.value}")
        print(f"  • Operador:    {res.operador}")
        print(f"  • Descripción: {res.descripcion}")
        if res.titular:
            print(f"  • Titular:     {res.titular.nombre} {res.titular.apellido}")
            print(f"  • Documento:   {res.titular.tipo_documento} {res.titular.nro_documento}")
        print("-" * 55)
        print("JSON PAYLOAD:")
        print(json.dumps(res.to_namespace_dict(), indent=2, ensure_ascii=False))
        print("=" * 55 + "\n")

    except FueraDeHorarioComercialException as e:
        print("\n" + "=" * 65)
        print("⚠️  RESTRICCIÓN DE HORARIO COMERCIAL OFICIAL (MOVISTAR IRIS)")
        print("=" * 65)
        print(f"  {e}")
        print("=" * 65 + "\n")
    except Exception as e:
        logger.error(f"Fallo durante la consulta de prueba: {e}", exc_info=True)


def cmd_test_batch(args):
    """Prueba el flujo completo de ProcesarLoteUseCase."""
    scraper_name = args.scraper.lower()
    batch_size = args.batch_size
    dry_run = args.dry_run

    print("\n" + "=" * 60)
    print(f"🧪 TEST DE LOTE - SCRAPER: {scraper_name.upper()} (Dry-run: {dry_run})")
    print("=" * 60)

    try:
        if dry_run:
            cola_repo = MemoryQueueAdapter([
                {"id": 99901, "ani": "2604275327", "dni": "33517690", "scraper_actual": scraper_name, "estado": "pendiente"},
                {"id": 99902, "ani": "2614556677", "scraper_actual": scraper_name, "estado": "pendiente"}
            ])
        else:
            cola_repo = MySQLQueueAdapter(pool_size=2, pool_name="cli_test_batch")

        scraper_kwargs = {"forzar_horario": args.forzar_horario}
        if getattr(args, "use_tor", None) is not None:
            scraper_kwargs["use_tor"] = args.use_tor
        if getattr(args, "use_proxy_pool", False):
            scraper_kwargs["use_proxy_pool"] = True
            scraper_kwargs["use_tor"] = False

        scraper = ScraperRegistry.obtener(scraper_name, **scraper_kwargs)
        scraper.iniciar()
        if not scraper.autenticar():
            print("❌ Error de autenticación.")
            return

        use_case = ProcesarLoteUseCase(cola_repo=cola_repo, scraper_engine=scraper)

        def on_item(item):
            print(f"  ➔ ID: {item['id']} | ANI: {item['ani']} | {item['status']} ➔ Siguiente: [{item['scraper_actual']}:{item['estado']}]")

        num_proc, sin_proc = use_case.ejecutar_lote(
            batch_size=batch_size,
            on_item_procesado=on_item,
            solo_sin_coincidencia=getattr(args, "solo_sin_coincidencia", False)
        )
        scraper.cerrar()

        print("-" * 60)
        print(f"Procesados con éxito: {num_proc} | Sin procesar: {len(sin_proc)}")
        print("=" * 60 + "\n")
    except FueraDeHorarioComercialException as e:
        print("\n" + "=" * 65)
        print("⚠️  RESTRICCIÓN DE HORARIO COMERCIAL OFICIAL (MOVISTAR IRIS)")
        print("=" * 65)
        print(f"  {e}")
        print("=" * 65 + "\n")


def cmd_sweep_orphans(args):
    """Ejecuta una pasada manual del centinela de huérfanos."""
    adapter = MySQLQueueAdapter(pool_size=2, pool_name="cli_sweep_pool")
    use_case = LiberarHuerfanosUseCase(cola_repo=adapter)
    rescatados = use_case.ejecutar(minutos_inactividad=args.minutos)
    print(f"\n🧹 Watchdog Sweeper: {rescatados} registros huérfanos recuperados a 'pendiente'.\n")


def cmd_supervise(args):
    """Inicia el supervisor industrial 24/7."""
    prio_val = None if args.prioridad == "auto" else int(args.prioridad)
    scraper_kwargs = {}
    if "browser" in args.scraper:
        scraper_kwargs["headless"] = not args.visible
    if getattr(args, "use_tor", None) is not None:
        scraper_kwargs["use_tor"] = args.use_tor
    if getattr(args, "use_proxy_pool", False):
        scraper_kwargs["use_proxy_pool"] = True
        scraper_kwargs["use_tor"] = False

    supervisor = SupervisorIndustrial(
        scraper_name=args.scraper.lower(),
        workers=args.workers,
        batch_size=args.batch_size,
        max_queries_worker=args.max_queries_worker,
        prioridad=prio_val,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        scraper_kwargs=scraper_kwargs,
        forzar_horario=args.forzar_horario,
        solo_sin_coincidencia=args.solo_sin_coincidencia
    )
    supervisor.ejecutar()


def main():
    parser = argparse.ArgumentParser(description="CLI Central de Scraping - Arquitectura Hexagonal")
    subparsers = parser.add_subparsers(dest="comando", help="Comando a ejecutar")

    # stats
    subparsers.add_parser("stats", help="Ver estado y métricas de la base de datos VPS")

    # list-scrapers
    subparsers.add_parser("list-scrapers", help="Ver scrapers disponibles")

    # test-line
    p_line = subparsers.add_parser("test-line", help="Consultar una línea individual")
    p_line.add_argument("ani", type=str, help="Número de teléfono (10 dígitos)")
    p_line.add_argument("--dni", type=str, default=None, help="DNI del titular (requerido para Claro Cobro Express)")
    p_line.add_argument("--scraper", type=str, default="iris_http", help="Scraper a utilizar (default: iris_http)")
    p_line.add_argument("--forzar-horario", action="store_true", help="Ignorar restricción de horario comercial oficial")
    p_line.add_argument("--tor", dest="use_tor", action="store_true", default=None, help="Forzar enrutamiento por Tor")
    p_line.add_argument("--no-tor", dest="use_tor", action="store_false", help="Deshabilitar proxy Tor (conexión directa)")
    p_line.add_argument("--proxy-pool", dest="use_proxy_pool", action="store_true", default=False, help="Usar pool de proxies públicos rotativos (alta velocidad)")

    # test-batch
    p_batch = subparsers.add_parser("test-batch", help="Ejecutar un micro-lote de prueba")
    p_batch.add_argument("--scraper", type=str, default="iris_http", help="Scraper a utilizar")
    p_batch.add_argument("--batch-size", type=int, default=3, help="Cantidad de registros")
    p_batch.add_argument("--dry-run", action="store_true", help="Usar cola en memoria sin tocar base de datos")
    p_batch.add_argument("--forzar-horario", action="store_true", help="Ignorar restricción de horario comercial oficial")
    p_batch.add_argument("--tor", dest="use_tor", action="store_true", default=None, help="Forzar enrutamiento por Tor")
    p_batch.add_argument("--no-tor", dest="use_tor", action="store_false", help="Deshabilitar proxy Tor (conexión directa)")
    p_batch.add_argument("--proxy-pool", dest="use_proxy_pool", action="store_true", default=False, help="Usar pool de proxies públicos rotativos (alta velocidad)")
    p_batch.add_argument("--solo-sin-coincidencia", action="store_true", default=False, help="Filtrar solo registros sin coincidencia en ninguna de las 3 compañías")

    # sweep-orphans
    p_sweep = subparsers.add_parser("sweep-orphans", help="Liberar registros colgados en procesando")
    p_sweep.add_argument("--minutos", type=int, default=15, help="Minutos de inactividad umbral (default: 15)")

    # supervise
    p_sup = subparsers.add_parser("supervise", help="Iniciar supervisor 24/7")
    p_sup.add_argument("--scraper", type=str, default="iris_http", help="Scraper a ejecutar")
    p_sup.add_argument("--workers", type=int, default=9, help="Cantidad de workers concurrentes")
    p_sup.add_argument("--batch-size", type=int, default=12, help="Lote por worker")
    p_sup.add_argument("--max-queries-worker", type=int, default=350, help="Rotación anti-leak")
    p_sup.add_argument("--prioridad", choices=["auto", "1", "2", "3"], default="auto", help="Estrategia de prioridad")
    p_sup.add_argument("--delay-min", type=float, default=1.5, help="Pausa mínima entre lotes")
    p_sup.add_argument("--delay-max", type=float, default=2.5, help="Pausa máxima entre lotes")
    p_sup.add_argument("--visible", action="store_true", help="Forzar navegador visible")
    p_sup.add_argument("--forzar-horario", action="store_true", help="Ignorar restricción de horario comercial oficial")
    p_sup.add_argument("--tor", dest="use_tor", action="store_true", default=None, help="Forzar enrutamiento por Tor Multi-Instance")
    p_sup.add_argument("--no-tor", dest="use_tor", action="store_false", help="Deshabilitar proxy Tor (conexión directa)")
    p_sup.add_argument("--proxy-pool", dest="use_proxy_pool", action="store_true", default=False, help="Usar pool de proxies públicos rotativos (alta velocidad)")
    p_sup.add_argument("--solo-sin-coincidencia", action="store_true", default=False, help="Filtrar solo registros sin coincidencia en ninguna de las 3 compañías (Claro, Personal, Movistar)")

    args = parser.parse_args()

    if args.comando == "stats":
        cmd_stats(args)
    elif args.comando == "list-scrapers":
        cmd_list_scrapers(args)
    elif args.comando == "test-line":
        cmd_test_line(args)
    elif args.comando == "test-batch":
        cmd_test_batch(args)
    elif args.comando == "sweep-orphans":
        cmd_sweep_orphans(args)
    elif args.comando == "supervise":
        cmd_supervise(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
