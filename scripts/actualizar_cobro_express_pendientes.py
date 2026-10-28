# -*- coding: utf-8 -*-
"""
Script de Actualización Segura en Lotes:
Pasa a 'pendiente' con prioridad 2 todos los registros de 'scraper_cobro_express'
con fecha_fin anterior al 15 de octubre de 2026.

Optimizaciones:
- Paginación basada en claves (id > last_id) usando el índice idx_cola_auto_estado.
- Actualización atómica por lotes de 1.000 IDs para prevenir desbordamiento de la tabla de bloqueos (Error 1206).
- Reseteo completo de campos de ejecución (resultado, error_msg, instance_id, fecha_inicio, fecha_fin)
  para asegurar compatibilidad total con VPSSyncAdapter y la cola de procesamiento.
"""
import os
import sys
import time
from datetime import datetime
if sys.stdout.encoding != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
if sys.stderr.encoding != 'utf-8':
    try: sys.stderr.reconfigure(encoding='utf-8')
    except Exception: pass

# Asegurar path de importación
sys.path.insert(0, os.path.abspath("."))
import config

import mysql.connector

CUTOFF_DATE = datetime(2026, 10, 15, 0, 0, 0)
AUTO_ID = "scraper_cobro_express"
TARGET_PC = "PC-01"
SCAN_BATCH_SIZE = 50000
UPDATE_CHUNK_SIZE = 1000

def get_connection():
    user = getattr(config, "COLA_AUTO_DBUSER", "automatizaciones")
    password = getattr(config, "COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q")
    return mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=user,
        password=password,
        database=config.VPS_DBNAME,
        use_pure=True,
        autocommit=False,
        connection_timeout=30
    )

def main():
    print("=" * 70, flush=True)
    print("ACTUALIZACIÓN DE REGISTROS 'scraper_cobro_express' A PENDIENTE", flush=True)
    print(f"Criterio: fecha_fin < {CUTOFF_DATE.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"Nueva prioridad: 2 | Nuevo estado: 'pendiente' | target_pc: '{TARGET_PC}'", flush=True)
    print("=" * 70, flush=True)

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    last_id = 0
    total_scanned = 0
    total_updated = 0
    start_time = time.time()
    batch_num = 0

    try:
        while True:
            batch_num += 1
            t_batch_start = time.time()

            # 1. Leer un bloque de registros completados
            query_scan = """
                SELECT id, fecha_fin
                FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
                WHERE auto_id = %s
                  AND estado = 'completado'
                  AND id > %s
                ORDER BY id ASC
                LIMIT %s
            """
            cur.execute(query_scan, (AUTO_ID, last_id, SCAN_BATCH_SIZE))
            rows = cur.fetchall()

            if not rows:
                print(f"\n[FIN DE ESCANEO] No se encontraron más registros completados para procesar.", flush=True)
                break

            total_scanned += len(rows)
            last_id = rows[-1]["id"]

            # 2. Filtrar filas que cumplen fecha_fin < CUTOFF_DATE
            ids_to_update = [
                r["id"] for r in rows 
                if r["fecha_fin"] is not None and r["fecha_fin"] < CUTOFF_DATE
            ]

            # 3. Actualizar en sub-lotes
            if ids_to_update:
                for i in range(0, len(ids_to_update), UPDATE_CHUNK_SIZE):
                    chunk = ids_to_update[i:i + UPDATE_CHUNK_SIZE]
                    placeholders = ", ".join(["%s"] * len(chunk))
                    query_update = f"""
                        UPDATE cola_automatizacion
                        SET estado = 'pendiente',
                            target_pc = '{TARGET_PC}',
                            prioridad = 2,
                            instance_id = NULL,
                            resultado = NULL,
                            error_msg = NULL,
                            fecha_inicio = NULL,
                            fecha_fin = NULL
                        WHERE id IN ({placeholders})
                    """
                    cur.execute(query_update, chunk)
                    conn.commit()

                total_updated += len(ids_to_update)

            elapsed = time.time() - start_time
            rate = total_scanned / elapsed if elapsed > 0 else 0

            # Log de progreso cada lote o si hubo updates
            if len(ids_to_update) > 0 or batch_num % 10 == 0:
                print(
                    f"Lote {batch_num:4d} | ID Cursor: {last_id:10d} | "
                    f"Escaneados: {total_scanned:9,d} | "
                    f"Actualizados: {total_updated:9,d} | "
                    f"Velocidad: {rate:7.0f} reg/s | "
                    f"Tiempo: {elapsed:5.1f}s",
                    flush=True
                )

    except Exception as e:
        print(f"\n[ERROR CRÍTICO] Durante la ejecución: {e}", flush=True)
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    total_time = time.time() - start_time
    print("=" * 70, flush=True)
    print(f"RESUMEN FINAL DE LA OPERACIÓN:", flush=True)
    print(f"  - Total registros escaneados:   {total_scanned:,}", flush=True)
    print(f"  - Total registros actualizados: {total_updated:,}", flush=True)
    print(f"  - Tiempo total de ejecución:    {total_time:.2f} segundos", flush=True)
    print("=" * 70, flush=True)

if __name__ == "__main__":
    main()
