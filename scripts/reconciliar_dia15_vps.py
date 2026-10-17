"""
Script de Reconciliación Industrial: Rescate y Re-sincronización de Datos Staging -> VPS Central
Arquitectura Hexagonal - Alta Velocidad por Primary Key
"""

import os
import sys
import time
import argparse
import logging
import sqlite3
import mysql.connector
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Configuración de codificación UTF-8 en Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("reconciliar_vps")

load_dotenv(PROJECT_ROOT / ".env")


def reconciliar(fecha: str = "2026-10-15", chunk_size: int = 2500, max_records: int = None, dry_run: bool = False):
    db_staging = PROJECT_ROOT / "data" / "staging_local.db"
    if not db_staging.exists():
        logger.error(f"Base de datos staging local no encontrada: {db_staging}")
        return

    logger.info("=" * 70)
    logger.info(f"INICIANDO RECONCILIACIÓN STAGING LOCAL -> VPS (Fecha filtro: {fecha})")
    logger.info("=" * 70)

    conn_sqlite = sqlite3.connect(str(db_staging))
    cur_sqlite = conn_sqlite.cursor()

    # Paso 1: Obtener solo los IDs (Index/Table scan ligero sin cargar JSON a memoria)
    where_fecha = ""
    params_sqlite = []
    if fecha and fecha.lower() != "todos":
        where_fecha = "AND fecha_procesado >= ? AND fecha_procesado <= ?"
        params_sqlite = [f"{fecha} 00:00:00", f"{fecha} 23:59:59"]

    logger.info("Extrayendo lista de IDs candidatos desde staging local...")
    t0_ids = time.time()
    cur_sqlite.execute(f"""
        SELECT id_vps
        FROM tareas_staging
        WHERE tipo_cola = 'cola_automatizacion'
          AND resultado_json IS NOT NULL 
          AND resultado_json != ''
          AND resultado_json NOT LIKE '%"status": "error"%'
          AND resultado_json NOT LIKE '%"status":"error"%'
          {where_fecha}
        ORDER BY id_vps ASC
    """, params_sqlite)

    ids = [r[0] for r in cur_sqlite.fetchall()]
    logger.info(f"Recuperados {len(ids):,} IDs válidos en {time.time() - t0_ids:.2f}s")

    if not ids:
        logger.info("No hay registros para reconciliar con ese filtro.")
        conn_sqlite.close()
        return

    if max_records and max_records < len(ids):
        ids = ids[:max_records]
        logger.info(f"Limitando proceso a los primeros {len(ids):,} registros.")

    # Conectar VPS
    conn_vps = mysql.connector.connect(
        host=os.getenv("VPS_DBHOST"),
        user=os.getenv("COLA_AUTO_DBUSER"),
        password=os.getenv("COLA_AUTO_DBPASS"),
        database=os.getenv("VPS_DBNAME"),
        port=int(os.getenv("VPS_DBPORT", 3306)),
        autocommit=False
    )
    cur_vps = conn_vps.cursor()

    query_vps = """
        UPDATE cola_automatizacion
        SET estado = 'completado',
            resultado = %s,
            error_msg = NULL,
            scrapers_intentados = %s,
            dni = COALESCE(NULLIF(dni, ''), %s),
            numero_de_linea = COALESCE(NULLIF(numero_de_linea, ''), %s),
            fecha_fin = COALESCE(%s, NOW())
        WHERE id = %s 
          AND (estado = 'pendiente' OR resultado IS NULL OR resultado = '');
    """

    total_actualizados = 0
    t_start = time.time()
    total_lotes = (len(ids) + chunk_size - 1) // chunk_size

    for i in range(0, len(ids), chunk_size):
        chunk_ids = ids[i:i + chunk_size]
        lote_num = (i // chunk_size) + 1

        # Paso 2: Leer los datos completos por Primary Key (ultrarrápido, ~0.5s)
        placeholders_pk = ",".join("?" for _ in chunk_ids)
        cur_sqlite.execute(f"""
            SELECT id_vps, numero_de_linea, dni, resultado_json, scrapers_intentados, fecha_procesado
            FROM tareas_staging
            WHERE tipo_cola = 'cola_automatizacion' AND id_vps IN ({placeholders_pk})
        """, chunk_ids)
        filas_chunk = cur_sqlite.fetchall()

        params_vps = []
        for item in filas_chunk:
            id_vps, linea, dni, res_json, scrapers, f_proc = item
            params_vps.append((
                res_json,
                scrapers or "scraper_cobro_express",
                dni,
                linea,
                f_proc,
                id_vps
            ))

        if not dry_run:
            cur_vps.executemany(query_vps, params_vps)
            conn_vps.commit()
            actualizados_lote = cur_vps.rowcount
            total_actualizados += actualizados_lote

            # Actualizar SQLite local a sincronizado
            cur_sqlite.execute(f"""
                UPDATE tareas_staging
                SET estado_local = 'sincronizado',
                    fecha_sincronizado = CURRENT_TIMESTAMP
                WHERE tipo_cola = 'cola_automatizacion' AND id_vps IN ({placeholders_pk})
            """, chunk_ids)
            conn_sqlite.commit()
        else:
            actualizados_lote = len(params_vps)
            total_actualizados += actualizados_lote

        pct = ((i + len(chunk_ids)) / len(ids)) * 100
        logger.info(
            f" [Lote {lote_num}/{total_lotes}] {len(chunk_ids):,} registros leídos | "
            f"Impactados en VPS: {actualizados_lote:,} (Progreso: {pct:.1f}%)"
        )

    t_total = time.time() - t_start
    velocidad = total_actualizados / t_total if t_total > 0 else 0

    logger.info("=" * 70)
    logger.info("RECONCILIACIÓN COMPLETADA CON ÉXITO")
    logger.info(f"Total registros procesados:   {len(ids):,}")
    logger.info(f"Total actualizados en VPS:    {total_actualizados:,}")
    logger.info(f"Tiempo total transcurrido:    {t_total:.2f} segundos")
    logger.info(f"Velocidad promedio:           {velocidad:.1f} registros/seg")
    logger.info("=" * 70)

    cur_vps.close()
    conn_vps.close()
    cur_sqlite.close()
    conn_sqlite.close()


def main():
    parser = argparse.ArgumentParser(description="Reconciliar datos locales de staging con el VPS central.")
    parser.add_argument("--fecha", type=str, default="2026-10-15", help="Fecha a reconciliar (YYYY-MM-DD o 'todos'). Def: 2026-10-15")
    parser.add_argument("--chunk-size", type=int, default=2500, help="Tamaño de chunk atómico executemany. Def: 2500")
    parser.add_argument("--max-records", type=int, default=None, help="Límite máximo de registros a enviar.")
    parser.add_argument("--dry-run", action="store_true", help="Simulación sin modificar la base de datos.")
    args = parser.parse_args()

    reconciliar(
        fecha=args.fecha,
        chunk_size=args.chunk_size,
        max_records=args.max_records,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
