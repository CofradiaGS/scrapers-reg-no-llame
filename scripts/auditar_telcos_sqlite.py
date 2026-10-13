#!/usr/bin/env python3
"""
Auditor Masivo de Telcos para Banco Macro SQLite.
Ejecuta 15 instancias/workers concurrentes sobre la red Tor (Stream Isolation)
para auditar en paralelo todas las líneas descubiertas por IRIS que están pendientes
de consulta en Claro, Personal y Movistar.
"""

import sys
import json
import time
import queue
import logging
import sqlite3
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
import concurrent.futures

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.claro.claro_adapter import ClaroAdapter
from adapters.scrapers.personal.personal_adapter import PersonalAdapter
from adapters.scrapers.movistar.movistar_adapter import MovistarAdapter

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"
LOG_FILE = PROJECT_ROOT / "auditor_telcos.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [TelcoAuditor]: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("TelcoAuditor")


class TelcoWorker:
    """Instancia de auditoría de Telcos con slot de Tor asignado."""
    def __init__(self, slot: int):
        self.slot = slot
        self.claro = ClaroAdapter(worker_slot=slot)
        self.personal = PersonalAdapter(worker_slot=slot)
        self.movistar = MovistarAdapter(worker_slot=slot)
        self.iniciado = False

    def iniciar(self):
        if not self.iniciado:
            self.claro.iniciar()
            self.personal.iniciar()
            self.movistar.iniciar()
            self.iniciado = True

    def cerrar(self):
        if self.iniciado:
            self.claro.cerrar()
            self.personal.cerrar()
            self.movistar.cerrar()
            self.iniciado = False

    def auditar_linea(self, ani: str, dni: str) -> dict:
        self.iniciar()
        linea_obj = Linea(ani=ani, dni=dni)
        operador_activo = None
        deuda_linea = 0.0
        info_linea = {}

        # 1. Claro
        try:
            res_c = self.claro.consultar_linea(linea_obj)
            if res_c.status == StatusScraping.COINCIDENCIA:
                operador_activo = "Claro"
                deuda_linea = float(res_c.detalles.get("deuda_total", 0.0))
                info_linea = res_c.detalles
        except Exception as e_c:
            logger.debug(f"[Slot {self.slot}] Aviso Claro {ani}: {e_c}")

        # 2. Personal
        if not operador_activo:
            try:
                res_p = self.personal.consultar_linea(linea_obj)
                if res_p.status == StatusScraping.COINCIDENCIA:
                    operador_activo = "Personal"
                    deuda_linea = float(res_p.detalles.get("deuda_total", 0.0))
                    info_linea = res_p.detalles
            except Exception as e_p:
                logger.debug(f"[Slot {self.slot}] Aviso Personal {ani}: {e_p}")

        # 3. Movistar
        if not operador_activo:
            try:
                res_m = self.movistar.consultar_linea(linea_obj)
                if res_m.status == StatusScraping.COINCIDENCIA:
                    operador_activo = "Movistar"
                    deuda_linea = float(res_m.detalles.get("deuda_total", 0.0))
                    info_linea = res_m.detalles
            except Exception as e_m:
                logger.debug(f"[Slot {self.slot}] Aviso Movistar {ani}: {e_m}")

        return {
            "ani": ani,
            "dni": dni,
            "operador": operador_activo or "Sin Coincidencia Telco",
            "tiene_deuda": deuda_linea > 0,
            "deuda_total": deuda_linea,
            "detalles_json": json.dumps(info_linea, ensure_ascii=False),
            "operador_activo": operador_activo
        }


class AuditorTelcosMasivo:
    def __init__(self, db_path: Path = DB_PATH, num_workers: int = 15):
        self.db_path = db_path
        self.num_workers = num_workers
        self.worker_queue: queue.Queue = queue.Queue()
        self.workers = [TelcoWorker(slot=i) for i in range(1, self.num_workers + 1)]
        for w in self.workers:
            self.worker_queue.put(w)
        logger.info(f"AuditorTelcosMasivo inicializado con {self.num_workers} slots de Tor Stream Isolation.")

    def get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def obtener_lineas_pendientes(self, limite: Optional[int] = None) -> List[Dict[str, str]]:
        conn = self.get_db()
        cursor = conn.cursor()
        query = """
            SELECT DISTINCT l.ani, l.dni
            FROM lineas_descubiertas l
            LEFT JOIN telcos_scraping t ON l.ani = t.ani
            WHERE t.id IS NULL
            ORDER BY l.id ASC
        """
        if limite:
            query += f" LIMIT {limite}"
        cursor.execute(query)
        rows = [{"ani": r["ani"], "dni": r["dni"]} for r in cursor.fetchall()]
        conn.close()
        return rows

    def auditar_item(self, item: Dict[str, str]) -> dict:
        worker = self.worker_queue.get()
        try:
            return worker.auditar_linea(item["ani"], item["dni"])
        finally:
            self.worker_queue.put(worker)

    def persistir_resultado(self, res: dict):
        conn = self.get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO telcos_scraping (
                    dni, ani, operador_detectado, tiene_deuda, deuda_total, detalles_json
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                res["dni"],
                res["ani"],
                res["operador"],
                1 if res["tiene_deuda"] else 0,
                res["deuda_total"],
                res["detalles_json"]
            ))

            # Actualizar resumen en personas si hubo coincidencia
            if res.get("operador_activo"):
                desc = f"{res['ani']}: {res['operador_activo']}"
                if res['deuda_total'] > 0:
                    desc += f" (${res['deuda_total']:.2f})"

                cursor.execute("SELECT lineas_activas_resumen FROM personas WHERE dni = ?", (res["dni"],))
                row = cursor.fetchone()
                prev = row[0] if row and row[0] else ""
                partes = [p.strip() for p in prev.split(" | ") if p.strip()] if prev else []
                if desc not in partes:
                    partes.append(desc)
                nuevo_resumen = " | ".join(partes)

                cursor.execute("""
                    UPDATE personas 
                    SET lineas_activas_resumen = ?, fecha_actualizacion = CURRENT_TIMESTAMP
                    WHERE dni = ?
                """, (nuevo_resumen, res["dni"]))

            conn.commit()
        except Exception as e:
            logger.error(f"Error persistiendo resultado de {res['ani']}: {e}")
        finally:
            conn.close()

    def procesar(self, limite: Optional[int] = None, lote_tamano: int = 50):
        pendientes = self.obtener_lineas_pendientes(limite=limite)
        total = len(pendientes)
        if total == 0:
            logger.info("🎉 No hay líneas pendientes de auditoría en Telcos.")
            return

        logger.info(f"🚀 Iniciando auditoría masiva de {total} líneas pendientes usando {self.num_workers} workers concurrentes...")
        completados = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            future_to_item = {executor.submit(self.auditar_item, it): it for it in pendientes}
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                completados += 1
                try:
                    res = future.result()
                    self.persistir_resultado(res)
                    op = res.get("operador_activo") or "Sin Coincidencia"
                    deuda = res.get("deuda_total", 0.0)
                    msg_deuda = f" (${deuda:.2f})" if deuda > 0 else ""
                    logger.info(f"[{completados}/{total}] {res['ani']} (DNI {res['dni']}): {op}{msg_deuda}")
                except Exception as e:
                    logger.error(f"[{completados}/{total}] Error en línea {item['ani']}: {e}")

        logger.info(f"✅ Auditoría finalizada: {completados}/{total} líneas procesadas.")

    def cerrar(self):
        for w in self.workers:
            w.cerrar()


def main():
    parser = argparse.ArgumentParser(description="Auditor Masivo Concurrente de Telcos")
    parser.add_argument("--workers", type=int, default=15, help="Cantidad de workers concurrentes (default: 15)")
    parser.add_argument("--limit", type=int, default=None, help="Límite máximo de líneas a auditar")
    parser.add_argument("--loop", action="store_true", help="Quedarse en bucle esperando nuevas líneas descubiertas")
    parser.add_argument("--interval", type=int, default=10, help="Segundos entre rondas en modo --loop")

    args = parser.parse_args()

    auditor = AuditorTelcosMasivo(num_workers=args.workers)
    try:
        if args.loop:
            logger.info(f"Modo continuo activo: consultando nuevas líneas cada {args.interval}s...")
            while True:
                auditor.procesar(limite=args.limit)
                time.sleep(args.interval)
        else:
            auditor.procesar(limite=args.limit)
    except KeyboardInterrupt:
        logger.info("Pausado por usuario (Ctrl+C).")
    finally:
        auditor.cerrar()


if __name__ == "__main__":
    main()
