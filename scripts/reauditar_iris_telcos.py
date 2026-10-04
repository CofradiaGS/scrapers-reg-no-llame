# -*- coding: utf-8 -*-
"""
Script de Re-auditoría Focalizada: IRIS Movistar + Telcos (Claro, Personal, Movistar)
Re-procesa de forma específica y segura los registros de data/banco_macro.sqlite
que tienen historial de trámites en IRIS o figuraban con 0 líneas por los bugs anteriores.

Ventajas clave:
1. Conserva intactos CUIT, Datuar y BCRA (no consume cuotas ni tiempo en lo ya obtenido).
2. Utiliza el nuevo motor IrisHttpBot con bypass de errores CW y extracción resiliente.
3. Audita en paralelo las líneas recuperadas en Claro, Personal y Movistar (Cobro Express vía Tor).
4. Persistencia atómica e idempotente en SQLite WAL con manejo robusto de Ctrl+C.
"""
import os
import sys
import time
import json
import signal
import queue
import sqlite3
import argparse
import logging
import threading
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.domain.entities import StatusScraping
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from scripts.procesar_banco_macro_sqlite import TelcoWorkerPool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ReauditorIrisTelcos")

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"


class ReauditorWorker:
    """Worker con sesión HTTP dedicada a IRIS Movistar."""
    def __init__(self, worker_id: int, orchestrator: "ReauditorOrchestrator"):
        self.worker_id = worker_id
        self.orchestrator = orchestrator
        if not orchestrator.skip_iris:
            self.iris = IrisHttpAdapter(forzar_horario=orchestrator.forzar_horario)
            self.iris.iniciar()
            self.iris.autenticar()
        else:
            self.iris = None

    def cerrar(self):
        if self.iris:
            self.iris.cerrar()

    def procesar_dni(self, dni: str, nombre_excel: str):
        prefix = f"[W{self.worker_id}] [{dni}]"
        logger.info(f"{prefix} Iniciando re-auditoría IRIS ({nombre_excel})...")

        lineas_descubiertas = []
        operaciones_iris = []
        error_iris = None

        if self.iris and not self.orchestrator.skip_iris:
            try:
                res_iris = self.iris.consultar_por_dni(dni)
                if res_iris.status == StatusScraping.COINCIDENCIA:
                    det_iris = res_iris.detalles or {}
                    lineas_descubiertas = det_iris.get("lineas_descubiertas") or []
                    operaciones_iris = det_iris.get("registros_historicos") or []
                    logger.info(f"{prefix} IRIS OK: {len(lineas_descubiertas)} líneas halladas en {len(operaciones_iris)} operaciones")
                else:
                    logger.info(f"{prefix} IRIS: Sin operaciones registradas")
            except Exception as e:
                logger.warning(f"{prefix} Error en IRIS: {e}")
                error_iris = f"Error IRIS: {e}"

        if self.orchestrator.stop_event.is_set():
            return

        # Telcos Cobro Express
        telcos_resultados = []
        lineas_activas_textos = []

        if self.orchestrator.telco_pool and not self.orchestrator.skip_telcos and lineas_descubiertas:
            num_hilos = min(len(lineas_descubiertas), self.orchestrator.telcos_workers)
            logger.info(f"{prefix} Auditando {len(lineas_descubiertas)} líneas en Telcos ({num_hilos} workers)...")
            resultados_pool = self.orchestrator.telco_pool.auditar_lineas_paralelo(lineas_descubiertas, dni)
            for res_item in resultados_pool:
                telco_reg = {
                    "ani": res_item["ani"],
                    "dni": res_item["dni"],
                    "operador": res_item["operador"],
                    "tiene_deuda": res_item["tiene_deuda"],
                    "deuda_total": res_item["deuda_total"],
                    "detalles_json": res_item["detalles_json"]
                }
                telcos_resultados.append(telco_reg)

                if res_item.get("operador_activo"):
                    op_act = res_item["operador_activo"]
                    deuda = res_item["deuda_total"]
                    desc_txt = f"{res_item['ani']}: {op_act}"
                    if deuda > 0:
                        desc_txt += f" (${deuda:.2f})"
                    lineas_activas_textos.append(desc_txt)

            logger.info(f"{prefix} Telcos OK: {len(lineas_activas_textos)} activas -> {lineas_activas_textos}")
        elif not lineas_descubiertas:
            logger.info(f"{prefix} Sin líneas telefónicas para auditar en Telcos")

        if self.orchestrator.stop_event.is_set():
            return

        # Persistir enriquecimiento
        self.orchestrator.persistir_resultado(
            dni=dni,
            operaciones_iris=operaciones_iris,
            lineas_descubiertas=lineas_descubiertas,
            telcos_resultados=telcos_resultados,
            lineas_activas_resumen=" | ".join(lineas_activas_textos),
            error_msg=error_iris
        )


class ReauditorOrchestrator:
    """Orquestador para la re-auditoría atómica de IRIS y Telcos."""
    def __init__(
        self,
        db_path: Path = DB_PATH,
        iris_workers: int = 5,
        telcos_workers: int = 15,
        forzar_horario: bool = True,
        skip_iris: bool = False,
        skip_telcos: bool = False
    ):
        self.db_path = db_path
        self.iris_workers = max(1, iris_workers)
        self.telcos_workers = max(1, telcos_workers)
        self.forzar_horario = forzar_horario
        self.skip_iris = skip_iris
        self.skip_telcos = skip_telcos

        self.db_write_lock = threading.Lock()
        self.local_thread = threading.local()
        self.stop_event = threading.Event()

        # Métricas
        self.stats_lock = threading.Lock()
        self.total_procesados = 0
        self.total_con_lineas = 0
        self.total_lineas_nuevas = 0
        self.total_lineas_activas = 0

        if not self.skip_telcos:
            self.telco_pool = TelcoWorkerPool(num_workers=self.telcos_workers, orchestrator=self)
        else:
            self.telco_pool = None

    def get_db(self) -> sqlite3.Connection:
        if not hasattr(self.local_thread, "conn"):
            conn = sqlite3.connect(str(self.db_path), timeout=60.0)
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 60000;")
            self.local_thread.conn = conn
        return self.local_thread.conn

    def persistir_resultado(
        self,
        dni: str,
        operaciones_iris: List[Dict[str, Any]],
        lineas_descubiertas: List[str],
        telcos_resultados: List[Dict[str, Any]],
        lineas_activas_resumen: str,
        error_msg: Optional[str] = None
    ):
        with self.db_write_lock:
            conn = self.get_db()
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 1. Obtener datos previos de la persona
            cursor.execute("SELECT datos_json, total_lineas_iris FROM personas WHERE dni = ?", (dni,))
            row = cursor.fetchone()
            raw_json = row[0] if row else None
            lineas_previas = row[1] if row and row[1] is not None else 0

            # Deserializar o armar dict base preservando CuitOnline / Datuar / BCRA
            datos_dict = {}
            if raw_json:
                try:
                    datos_dict = json.loads(raw_json)
                except Exception:
                    datos_dict = {}

            # Enriquecer o actualizar namespace 'iris'
            datos_dict["iris"] = {
                "total_lineas": len(lineas_descubiertas),
                "total_operaciones": len(operaciones_iris),
                "lineas_descubiertas": lineas_descubiertas,
                "ultima_reauditoria": now_str
            }

            # Enriquecer o actualizar namespace 'telcos'
            if telcos_resultados:
                datos_dict["telcos"] = {
                    "auditadas": len(telcos_resultados),
                    "activas_resumen": lineas_activas_resumen,
                    "resultados": telcos_resultados
                }

            datos_json_str = json.dumps(datos_dict, ensure_ascii=False)

            # 2. Actualizar tabla personas
            cursor.execute("""
                UPDATE personas
                SET total_lineas_iris = ?,
                    lineas_activas_resumen = ?,
                    datos_json = ?,
                    fecha_actualizacion = ?
                WHERE dni = ?
            """, (
                len(lineas_descubiertas),
                lineas_activas_resumen,
                datos_json_str,
                now_str,
                dni
            ))

            # 3. Actualizar operaciones_iris
            cursor.execute("DELETE FROM operaciones_iris WHERE dni = ?", (dni,))
            for op in operaciones_iris:
                det = op.get("detalle", {})
                cursor.execute("""
                    INSERT INTO operaciones_iris (
                        dni, nro_tramite_abd, id_tramite_spn, tipo_operacion,
                        operador_receptor, fecha_operacion, estado, producto, tecnologia
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    dni,
                    det.get("nro_tramite_abd") or op.get("nro_tramite"),
                    det.get("id_tramite_spn"),
                    op.get("operacion"),
                    det.get("operador_receptor"),
                    det.get("fecha_operacion") or op.get("fecha_alta"),
                    det.get("estado") or op.get("estado"),
                    det.get("producto") or op.get("producto"),
                    det.get("tecnologia")
                ))

            # 4. Insertar líneas descubiertas
            for linea_str in lineas_descubiertas:
                cursor.execute("""
                    INSERT OR IGNORE INTO lineas_descubiertas (dni, ani, origen_extraccion)
                    VALUES (?, ?, 'iris_port_out')
                """, (dni, linea_str))

            # 5. Insertar resultados de Telcos
            if telcos_resultados:
                cursor.execute("DELETE FROM telcos_scraping WHERE dni = ?", (dni,))
                for t in telcos_resultados:
                    cursor.execute("""
                        INSERT INTO telcos_scraping (
                            dni, ani, operador_detectado, tiene_deuda, deuda_total, detalles_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        dni, t["ani"], t["operador"], 1 if t["tiene_deuda"] else 0,
                        t["deuda_total"], t["detalles_json"]
                    ))

            conn.commit()

            # Actualizar contadores globales
            with self.stats_lock:
                self.total_procesados += 1
                if len(lineas_descubiertas) > 0:
                    self.total_con_lineas += 1
                if len(lineas_descubiertas) > lineas_previas:
                    delta = len(lineas_descubiertas) - lineas_previas
                    self.total_lineas_nuevas += delta
                    logger.info(f"✨ [DNI {dni}] ¡RECUPERADAS {delta} LÍNEAS NUEVAS! (Total actual: {len(lineas_descubiertas)})")
                if lineas_activas_resumen:
                    self.total_lineas_activas += len(lineas_activas_resumen.split(" | "))

    def obtener_candidatos(
        self,
        modo: str = "all-ops",
        dni_puntual: Optional[str] = None,
        limite: Optional[int] = None
    ) -> List[tuple]:
        """Obtiene la lista de DNIs a re-auditar según el modo seleccionado."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        if dni_puntual:
            cursor.execute("SELECT dni, nombre_excel FROM personas WHERE dni = ?", (dni_puntual,))
            return cursor.fetchall()

        if modo == "zero-lines-only":
            # Estrategia 1: Casos que tienen operaciones registradas pero 0 líneas
            cursor.execute("""
                SELECT DISTINCT p.dni, p.nombre_excel
                FROM personas p
                JOIN operaciones_iris o ON p.dni = o.dni
                WHERE p.total_lineas_iris = 0
                ORDER BY p.dni ASC
            """)
        elif modo == "all-zero-lines":
            # Estrategia 3: Todos los registros completados que tengan 0 líneas
            cursor.execute("""
                SELECT dni, nombre_excel
                FROM personas
                WHERE estado_proceso = 'completado' AND total_lineas_iris = 0
                ORDER BY dni ASC
            """)
        else:
            # Estrategia 2 (Default): Todos los que tienen trámites en IRIS
            cursor.execute("""
                SELECT DISTINCT p.dni, p.nombre_excel
                FROM personas p
                JOIN operaciones_iris o ON p.dni = o.dni
                ORDER BY p.dni ASC
            """)

        rows = cursor.fetchall()
        conn.close()

        if limite and limite > 0:
            rows = rows[:limite]

        return rows

    def ejecutar(self, modo: str = "all-ops", dni_puntual: Optional[str] = None, limite: Optional[int] = None):
        candidatos = self.obtener_candidatos(modo=modo, dni_puntual=dni_puntual, limite=limite)
        total = len(candidatos)

        if total == 0:
            print("ℹ️ No hay registros que coincidan con el filtro seleccionado.")
            return

        print(f"\n{'='*75}")
        print(f"🚀 Iniciando RE-AUDITORÍA FOCALIZADA DE IRIS + TELCOS ({total} personas)")
        print(f"⚡ Modo: {modo.upper()} | Workers IRIS: {self.iris_workers} | Workers Telcos: {self.telcos_workers}")
        print(f"🛡️  Datos de CuitOnline, Datuar y BCRA protegidos e inalterables.")
        print(f"{'='*75}\n")

        cola_trabajo = queue.Queue()
        for idx, item in enumerate(candidatos, 1):
            cola_trabajo.put((idx, item[0], item[1]))

        def vaciar_cola():
            while not cola_trabajo.empty():
                try:
                    cola_trabajo.get_nowait()
                    cola_trabajo.task_done()
                except queue.Empty:
                    break

        def worker_loop(w_id: int):
            worker = ReauditorWorker(worker_id=w_id, orchestrator=self)
            try:
                while not self.stop_event.is_set():
                    try:
                        num_act, dni, nombre_excel = cola_trabajo.get(timeout=1.0)
                    except queue.Empty:
                        break

                    if self.stop_event.is_set():
                        cola_trabajo.task_done()
                        break

                    print(f"\n🔍 [W{w_id}] [{num_act}/{total}] Re-auditando DNI {dni} | {nombre_excel}")
                    try:
                        worker.procesar_dni(dni, nombre_excel)
                    except Exception as e:
                        logger.error(f"[W{w_id}] Error en {dni}: {e}")
                    finally:
                        cola_trabajo.task_done()
            finally:
                worker.cerrar()

        def sigint_handler(sig, frame):
            print("\n\n🛑 [CTRL+C] Interrupción de usuario detectada. Deteniendo de inmediato...")
            self.stop_event.set()
            vaciar_cola()

        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, sigint_handler)
        if hasattr(signal, "SIGBREAK"):
            original_sigbreak = signal.getsignal(signal.SIGBREAK)
            signal.signal(signal.SIGBREAK, sigint_handler)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.iris_workers)
        futures = [executor.submit(worker_loop, i) for i in range(1, self.iris_workers + 1)]

        try:
            while any(not f.done() for f in futures):
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n\n🛑 [CTRL+C] KeyboardInterrupt capturado.")
            self.stop_event.set()
            vaciar_cola()
        finally:
            signal.signal(signal.SIGINT, original_sigint)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, original_sigbreak)

            if self.stop_event.is_set():
                vaciar_cola()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        print("\n" + "="*75)
        if self.stop_event.is_set():
            print("🛑 Re-auditoría detenida por el usuario. Base SQLite intacta y actualizada.")
        else:
            print("🎉 ¡RE-AUDITORÍA FINALIZADA EXITOSAMENTE!")
        print(f"📊 Métricas:")
        print(f"   - Personas procesadas:        {self.total_procesados}/{total}")
        print(f"   - Personas con líneas:        {self.total_con_lineas}")
        print(f"   - Líneas nuevas recuperadas:  {self.total_lineas_nuevas}")
        print(f"   - Líneas activas en Telcos:   {self.total_lineas_activas}")
        print("="*75)

    def cerrar(self):
        if self.telco_pool:
            self.telco_pool.cerrar()


def main():
    parser = argparse.ArgumentParser(description="Re-auditor Focalizado IRIS + Telcos (Banco Macro)")
    parser.add_argument("--modo", choices=["all-ops", "zero-lines-only", "all-zero-lines"], default="all-ops",
                        help="all-ops: Estrategia 2 (Default: todos los que tienen historial en IRIS, ~455 DNIs) | zero-lines-only: Estrategia 1 (los 36 que tienen historial pero 0 líneas) | all-zero-lines: Estrategia 3 (los 1003 con 0 líneas)")
    parser.add_argument("--zero-lines-only", action="store_true", help="Atajo para --modo zero-lines-only (Estrategia 1)")
    parser.add_argument("--dni", type=str, default=None, help="Re-auditar un DNI puntual para prueba")
    parser.add_argument("--limit", type=int, default=None, help="Cantidad máxima de DNIs a procesar")
    parser.add_argument("--iris-workers", type=int, default=5, help="Sesiones HTTP concurrentes en IRIS (default: 5)")
    parser.add_argument("--telcos-workers", type=int, default=15, help="Workers concurrentes de Telcos con Tor (default: 15)")
    parser.add_argument("--skip-telcos", action="store_true", help="Omitir auditoría en Telcos y solo actualizar IRIS")
    parser.add_argument("--no-forzar-horario", action="store_true", help="Respetar horario comercial estricto de IRIS (08-20h)")

    args = parser.parse_args()

    modo = "zero-lines-only" if args.zero_lines_only else args.modo

    reauditor = ReauditorOrchestrator(
        iris_workers=args.iris_workers,
        telcos_workers=args.telcos_workers,
        forzar_horario=not args.no_forzar_horario,
        skip_telcos=args.skip_telcos
    )

    try:
        reauditor.ejecutar(modo=modo, dni_puntual=args.dni, limite=args.limit)
    finally:
        reauditor.cerrar()


if __name__ == "__main__":
    main()
