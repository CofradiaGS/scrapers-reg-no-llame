# -*- coding: utf-8 -*-
"""
Script CLI: Normalizador Masivo de DNI en VPS — Versión Optimizada
Delega el filtro y la extracción del DNI íntegramente a MySQL (JSON_EXTRACT).
No transfiere datos al cliente Python — el UPDATE se ejecuta directamente en el servidor.

Estrategia:
  - Filtra solo registros con datos_json que contengan 'nro_documento' o 'dni' en el nodo IRIS.
  - Ejecuta UPDATE ... SET dni = JSON_UNQUOTE(JSON_EXTRACT(...)) en batches atómicos por id.

Uso:
  python scripts/normalize_vps_dni.py [--batch-size 5000]
"""
import os
import sys
import time
import signal
import argparse
import logging
import mysql.connector

sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("NormalizeVPS_DNI")

stop_requested = False

def sigint_handler(signum, frame):
    global stop_requested
    print("\n🛑 Señal de interrupción recibida. Finalizando lote actual de forma segura...")
    stop_requested = True

signal.signal(signal.SIGINT, sigint_handler)


def asegurar_columna_dni(conn, table: str):
    cursor = conn.cursor()
    try:
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'dni'")
        if not cursor.fetchone():
            logger.info(f"➕ Columna 'dni' ausente. Creando con ALGORITHM=INSTANT...")
            try:
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `dni` VARCHAR(15) NULL DEFAULT NULL, ALGORITHM=INSTANT")
                conn.commit()
                logger.info("✅ Columna 'dni' creada (ALGORITHM=INSTANT).")
            except Exception as e_col:
                logger.warning(f"Fallback INSTANT: {e_col}. Intentando estándar...")
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `dni` VARCHAR(15) NULL DEFAULT NULL")
                conn.commit()
            try:
                cursor.execute(f"ALTER TABLE `{table}` ADD INDEX `idx_dni` (`dni`), ALGORITHM=INPLACE, LOCK=NONE")
                conn.commit()
                logger.info("✅ Índice 'idx_dni' creado.")
            except Exception as e_idx:
                logger.warning(f"No se pudo crear índice idx_dni: {e_idx}")
        else:
            logger.info(f"✔️  Columna 'dni' ya existe.")
    finally:
        cursor.close()


def contar_candidatos(conn, table: str) -> int:
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            SELECT COUNT(*) FROM `{table}`
            WHERE dni IS NULL
              AND (
                   datos_json LIKE '%nro_documento%'
                OR datos_json LIKE '%\"dni\"%'
              )
        """)
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        cursor.close()


def normalizar_dnis_vps(batch_size: int = 5000):
    logger.info("🚀 Iniciando normalización masiva de DNI (modo server-side SQL)...")
    table = config.VPS_DB_TABLE

    conn = mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=config.VPS_DBUSER,
        password=config.VPS_DBPASS,
        database=config.VPS_DBNAME,
        autocommit=False
    )

    asegurar_columna_dni(conn, table)

    total_candidatos = contar_candidatos(conn, table)
    logger.info(f"📊 Registros candidatos (con nro_documento/dni en datos_json, sin dni relacional): {total_candidatos:,}")
    if total_candidatos == 0:
        logger.info("🎉 No hay registros pendientes de normalización de DNI.")
        conn.close()
        return

    cursor = conn.cursor()
    actualizados_total = 0
    last_id = 0
    t0 = time.time()

    # Expresiones JSON_EXTRACT para cada posible ubicación del DNI dentro de datos_json:
    # 1. iris.titular.nro_documento  (fuente principal: IRIS port out)
    # 2. iris.titular.dni
    # 3. iris.detalles.dni
    # 4. datuar.dni / datuar.detalles.dni
    # 5. cuitonline.dni / cuitonline.detalles.dni
    COALESCE_EXPR = """
        COALESCE(
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.iris.titular.nro_documento')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.iris.titular.dni')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.iris.detalles.dni')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.datuar.detalles.dni')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.datuar.dni')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.cuitonline.detalles.dni')), 'null'),
            NULLIF(JSON_UNQUOTE(JSON_EXTRACT(datos_json, '$.cuitonline.dni')), 'null')
        )
    """

    try:
        while not stop_requested:
            # SELECT solo IDs del siguiente bloque candidato (sin traer datos_json al cliente)
            cursor.execute(f"""
                SELECT id FROM `{table}`
                WHERE id > %s
                  AND dni IS NULL
                  AND (
                       datos_json LIKE '%nro_documento%'
                    OR datos_json LIKE '%\"dni\"%'
                  )
                ORDER BY id ASC
                LIMIT %s
            """, (last_id, batch_size))
            ids = [row[0] for row in cursor.fetchall()]

            if not ids:
                break

            last_id = ids[-1]
            id_min, id_max = ids[0], ids[-1]

            # UPDATE delegado íntegramente al servidor MySQL
            cursor.execute(f"""
                UPDATE `{table}`
                SET dni = REGEXP_REPLACE(
                    {COALESCE_EXPR},
                    '[^0-9]', ''
                )
                WHERE id BETWEEN {id_min} AND {id_max}
                  AND dni IS NULL
                  AND {COALESCE_EXPR} IS NOT NULL
            """)
            conn.commit()
            afectados = cursor.rowcount
            actualizados_total += afectados

            elapsed = round(time.time() - t0, 1)
            rpm = round((actualizados_total / elapsed) * 60, 0) if elapsed > 0 else 0
            logger.info(
                f"📈 Bloque ID {id_min:,}–{id_max:,} | "
                f"✨ Lote: {afectados} | "
                f"Total: {actualizados_total:,} DNI guardados | "
                f"⚡ {rpm:,.0f} upd/min"
            )

    except Exception as e:
        logger.error(f"❌ Error durante la normalización: {e}")
        conn.rollback()
        raise
    finally:
        elapsed_total = round(time.time() - t0, 2)
        logger.info("==================================================")
        logger.info("🏆 NORMALIZACIÓN DE DNI FINALIZADA")
        logger.info(f"⏱️  Tiempo total: {elapsed_total}s")
        logger.info(f"✅  DNI guardados en columna física: {actualizados_total:,}")
        logger.info("==================================================")
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Normalizador masivo de DNI para VPS Central (server-side SQL)")
    parser.add_argument("--batch-size", type=int, default=5000, help="IDs por lote de UPDATE (default: 5000)")
    args = parser.parse_args()
    normalizar_dnis_vps(batch_size=args.batch_size)


if __name__ == "__main__":
    main()
