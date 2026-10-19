# -*- coding: utf-8 -*-
"""
Script de Ingesta / Alimentación: Cobro Express -> ENACOM Web (cola_automatizacion)
Transfiere registros procesados en el mes vigente por 'scraper_cobro_express'
que hayan resultado en 'sin_coincidencia' en las 3 operadoras (Claro, Personal y Movistar)
hacia nuevas tareas independientes con auto_id = 'scraper_enacom' para PC-08.

Características técnicas:
- Inserciones en lotes atómicos de hasta 5.000 registros por commit.
- Idempotencia garantizada vía UNIQUE KEY (auto_id, numero_de_linea) con INSERT IGNORE.
- dias_desde_ultimo_cambio = NULL (solicitud explícita del operador).
- Preservación íntegra de payload, prioridad, dni y paso_por_iris.
- Trazabilidad con scrapers_intentados = 'scraper_cobro_express'.
- Paginación basada en claves (WHERE c.id > last_id) para latencias mínimas sobre tablas de 70M filas.

Uso:
    python scripts/alimentar_enacom_desde_cobro_express.py --dry-run
    python scripts/alimentar_enacom_desde_cobro_express.py --batch-size 5000 --target-pc PC-08
    python scripts/alimentar_enacom_desde_cobro_express.py --limit 10000
"""

import sys
import os
import time
import json
import argparse
from datetime import datetime
from typing import Dict, Any, List, Optional

# Asegurar UTF-8 en stdout/stderr para Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Asegurar path raíz del proyecto
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def conectar_bd():
    """Establece conexión limpia con MySQL VPS central."""
    return mysql.connector.connect(
        host=os.getenv("VPS_DBHOST", "172.16.20.15"),
        user=os.getenv("COLA_AUTO_DBUSER", "automatizaciones"),
        password=os.getenv("COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
        database=os.getenv("VPS_DBNAME", "bases"),
        port=int(os.getenv("VPS_DBPORT", 3306)),
        use_pure=True,
        autocommit=False,
        connection_timeout=30
    )


def obtener_id_minimo_mes_vigente(conn, fecha_inicio_mes: str) -> int:
    """
    Obtiene el ID mínimo con fecha_fin en el mes vigente utilizando el índice idx_fecha_fin
    para que la paginación principal corra sobre rangos de IDs exactos sin full-table-scans.
    """
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT id 
        FROM cola_automatizacion 
        WHERE fecha_fin >= %s 
        ORDER BY fecha_fin ASC 
        LIMIT 1;
    """
    cursor.execute(query, (fecha_inicio_mes,))
    row = cursor.fetchone()
    cursor.close()
    if row and row.get("id"):
        return row["id"]
    # Fallback seguro: buscar fecha_creacion
    cursor = conn.cursor(dictionary=True)
    query_fallback = """
        SELECT id 
        FROM cola_automatizacion 
        WHERE fecha_creacion >= %s 
        ORDER BY id ASC 
        LIMIT 1;
    """
    cursor.execute(query_fallback, (fecha_inicio_mes,))
    row_fb = cursor.fetchone()
    cursor.close()
    return row_fb["id"] if row_fb and row_fb.get("id") else 0


def alimentar_enacom(
    batch_size: int = 5000,
    target_pc: str = "PC-08",
    limit_total: Optional[int] = None,
    dry_run: bool = False,
    fecha_mes: Optional[str] = None
):
    start_time = time.time()
    ahora = datetime.now()
    if not fecha_mes:
        fecha_mes = ahora.strftime("%Y-%m-01 00:00:00")

    print("=" * 80)
    print("ALIMENTADOR COLA AUTOMATIZACIÓN: Cobro Express -> ENACOM Web")
    print(f"Fecha/Hora inicio:       {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mes vigente filtro:      >= {fecha_mes}")
    print(f"Tamaño de lote (batch):  {batch_size:,} registros")
    print(f"Target PC destino:       {target_pc}")
    print(f"Límite total a insertar: {f'{limit_total:,}' if limit_total else 'Sin límite (todos los elegibles)'}")
    print(f"Modo DRY-RUN:            {'SÍ (Simulación sin escrituras)' if dry_run else 'NO (Producción activa)'}")
    print("=" * 80)

    conn = conectar_bd()

    # 1. Determinar ID base del mes
    print("\n🔍 Calculando ID inicial para el mes vigente...")
    min_id = obtener_id_minimo_mes_vigente(conn, fecha_mes)
    print(f"   -> ID mínimo detectado para {fecha_mes[:7]}: {min_id:,}")

    # Query de selección por lotes (Keyset pagination ultra-rápida por Clustered Primary Index)
    query_select = """
        SELECT c.id, c.numero_de_linea, c.dni, c.prioridad, c.target_pc,
               c.scrapers_intentados, c.paso_por_iris, c.payload
        FROM cola_automatizacion c FORCE INDEX (PRIMARY)
        WHERE c.id > %s
          AND c.auto_id = 'scraper_cobro_express'
          AND c.estado = 'completado'
          AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.claro.status')) = 'sin_coincidencia'
          AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.personal.status')) = 'sin_coincidencia'
          AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.movistar.status')) = 'sin_coincidencia'
        LIMIT %s;
    """

    # Query de inserción atómica
    query_insert = """
        INSERT IGNORE INTO cola_automatizacion (
            prioridad,
            target_pc,
            estado,
            auto_id,
            instance_id,
            payload,
            resultado,
            error_msg,
            intentos,
            fecha_creacion,
            fecha_inicio,
            fecha_fin,
            dias_desde_ultimo_cambio,
            numero_de_linea,
            dni,
            fecha_actualizacion,
            scrapers_intentados,
            paso_por_iris
        ) VALUES (
            %s, %s, %s, %s, NULL, %s, NULL, NULL, 0, NOW(), NULL, NULL, %s, %s, %s, NOW(), %s, %s
        );
    """

    total_leidos = 0
    total_insertados = 0
    total_ignorados_duplicados = 0
    lote_num = 0
    ultimo_id = min_id - 1

    cur_read = conn.cursor(dictionary=True)
    cur_write = conn.cursor()

    try:
        while True:
            # Calcular cuánto pedir en este lote
            pedir_lote = batch_size
            if limit_total:
                restante = limit_total - total_insertados
                if restante <= 0:
                    print(f"\n[INFO] Se alcanzó el límite total configurado ({limit_total:,} registros).")
                    break
                pedir_lote = min(batch_size, restante)

            t0_batch = time.time()
            cur_read.execute(query_select, (ultimo_id, pedir_lote))
            filas = cur_read.fetchall()

            if not filas:
                print(f"\n[INFO] No se encontraron más registros elegibles pendientes para transferir.")
                break

            lote_num += 1
            total_leidos += len(filas)
            ultimo_id = filas[-1]["id"]

            buffer_insert = []
            for r in filas:
                # Extraer número de línea limpio (10 dígitos)
                num_linea = str(r.get("numero_de_linea") or "").strip()
                pay_raw = r.get("payload")
                pay_dict = pay_raw if isinstance(pay_raw, dict) else {}
                if isinstance(pay_raw, str):
                    try:
                        pay_dict = json.loads(pay_raw)
                    except Exception:
                        pay_dict = {}

                if not num_linea or len(num_linea) != 10:
                    cand = str(pay_dict.get("numero_de_linea") or pay_dict.get("ani") or pay_dict.get("linea") or "").strip()
                    cand_dig = "".join(filter(str.isdigit, cand))
                    if len(cand_dig) == 10:
                        num_linea = cand_dig

                # Extraer DNI limpio
                dni_val = str(r.get("dni") or pay_dict.get("dni") or "").strip()
                dni_limpio = "".join(filter(str.isdigit, dni_val)) if dni_val else None

                # Prioridad (default 2)
                prio = r.get("prioridad")
                if prio is None:
                    prio = 2

                # Payload en formato JSON string
                payload_str = json.dumps(pay_dict, ensure_ascii=False) if pay_dict else json.dumps(r.get("payload") or {}, ensure_ascii=False)

                # Paso por iris
                paso_iris = r.get("paso_por_iris")
                if paso_iris is None:
                    paso_iris = 0

                # Tupla para el INSERT:
                # prioridad, target_pc, estado, auto_id, payload, dias_desde_ultimo_cambio, numero_de_linea, dni, scrapers_intentados, paso_por_iris
                record = (
                    prio,
                    target_pc,
                    "pendiente",
                    "scraper_enacom",
                    payload_str,
                    None,  # dias_desde_ultimo_cambio = NULL según especificación del usuario
                    num_linea,
                    dni_limpio,
                    "scraper_cobro_express",
                    paso_iris
                )
                buffer_insert.append(record)

            # Inserción atómica del lote
            if not dry_run:
                cur_write.executemany(query_insert, buffer_insert)
                conn.commit()
                insertadas_este_lote = cur_write.rowcount
                ignoradas_este_lote = len(buffer_insert) - insertadas_este_lote
            else:
                insertadas_este_lote = len(buffer_insert)
                ignoradas_este_lote = 0

            total_insertados += insertadas_este_lote
            total_ignorados_duplicados += ignoradas_este_lote
            t_lote = round(time.time() - t0_batch, 2)

            print(
                f"   [Lote #{lote_num:03d}] Leídos: {len(filas):,d} | "
                f"Insertados: {insertadas_este_lote:,d} | "
                f"Ignorados/Dupl: {ignoradas_este_lote:,d} | "
                f"Último ID: {ultimo_id} | "
                f"Tiempo: {t_lote}s",
                flush=True
            )

            # Si el lote fue menor al pedido, no quedan más
            if len(filas) < pedir_lote:
                print("\n[INFO] Se alcanzó el final de los registros elegibles.", flush=True)
                break

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR CRÍTICO] Falló el procesamiento del lote #{lote_num}: {e}")
        raise
    finally:
        cur_read.close()
        cur_write.close()
        conn.close()

    elapsed = round(time.time() - start_time, 2)
    rate = round(total_insertados / elapsed, 1) if elapsed > 0 else 0

    print("\n" + "=" * 80)
    print("RESUMEN DE EJECUCIÓN - ALIMENTACIÓN ENACOM")
    print("=" * 80)
    print(f"Total registros leídos de Cobro Express: {total_leidos:,}")
    print(f"Total nuevas tareas insertadas:         {total_insertados:,}")
    print(f"Total ignorados por duplicado previo:   {total_ignorados_duplicados:,}")
    print(f"Lotes procesados:                       {lote_num}")
    print(f"Tiempo total:                           {elapsed}s")
    print(f"Velocidad efectiva:                     {rate} reg/s")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alimentador de cola_automatizacion para scraper_enacom desde Cobro Express.")
    parser.add_argument("--batch-size", type=int, default=5000, help="Tamaño de lote para el INSERT (default: 5000)")
    parser.add_argument("--target-pc", type=str, default="PC-08", help="Target PC asignado (default: PC-08)")
    parser.add_argument("--limit", type=int, default=None, help="Límite máximo total de registros a transferir")
    parser.add_argument("--dry-run", action="store_true", help="Simula la operación sin escribir en MySQL")
    parser.add_argument("--mes-vigente", type=str, default=None, help="Fecha inicio mes (default: primer día mes actual 00:00:00)")

    args = parser.parse_args()

    alimentar_enacom(
        batch_size=args.batch_size,
        target_pc=args.target_pc,
        limit_total=args.limit,
        dry_run=args.dry_run,
        fecha_mes=args.mes_vigente
    )
