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


def calcular_fecha_desde(meses: int = 2) -> str:
    """Calcula el primer día (00:00:00) de la ventana de los últimos N meses vigentes."""
    ahora = datetime.now()
    mes = ahora.month - (meses - 1)
    anio = ahora.year
    while mes < 1:
        mes += 12
        anio -= 1
    return f"{anio:04d}-{mes:02d}-01 00:00:00"


from pathlib import Path

CURSOR_FILE = Path(__file__).resolve().parent.parent / "data" / "cursor_alimentacion_ce_enacom.json"


SQL_FILTRO_P1 = "(c.numero_de_linea LIKE '11%' OR c.numero_de_linea LIKE '260%' OR c.numero_de_linea LIKE '261%' OR c.numero_de_linea LIKE '262%' OR c.numero_de_linea LIKE '263%')"

SQL_FILTRO_P2 = """(
    c.numero_de_linea LIKE '264%' OR c.numero_de_linea LIKE '265%' OR c.numero_de_linea LIKE '266%'
    OR c.numero_de_linea LIKE '380%' OR c.numero_de_linea LIKE '382%'
    OR c.numero_de_linea LIKE '280%' OR c.numero_de_linea LIKE '299%' OR c.numero_de_linea LIKE '298%'
    OR c.numero_de_linea LIKE '297%' OR c.numero_de_linea LIKE '294%' OR c.numero_de_linea LIKE '291%'
    OR c.numero_de_linea LIKE '2901%' OR c.numero_de_linea LIKE '2966%' OR c.numero_de_linea LIKE '2964%'
    OR c.numero_de_linea LIKE '2920%'
)"""

SQL_FILTRO_P3 = f"""NOT (
    {SQL_FILTRO_P1} OR {SQL_FILTRO_P2}
)"""


def calcular_prioridad_enacom(numero_de_linea: str) -> int:
    """
    Calcula la prioridad geográfica para ENACOM Web según la regla de negocio:
    - Prioridad 1: Buenos Aires / AMBA (11) y Mendoza completa (260, 261, 262, 263).
    - Prioridad 2: Cuyo (264, 265, 266, 380, 382) y Sur / Patagonia (280, 299, 298, 297, 294, 291, 2901, 2966, 2964, 2920).
    - Prioridad 3: Resto del país.
    """
    num = "".join(filter(str.isdigit, str(numero_de_linea or "")))
    if num.startswith(('11', '260', '261', '262', '263')):
        return 1
    elif num.startswith((
        '264', '265', '266', '380', '382',
        '280', '299', '298', '297', '294',
        '291', '2901', '2966', '2964', '2920'
    )):
        return 2
    else:
        return 3


def obtener_cursores_guardados(min_id: int) -> Dict[str, int]:
    """Recupera los cursores por nivel de prioridad para paginación incremental."""
    base = min_id - 1
    cursores = {"prio_1": base, "prio_2": base, "prio_3": base}
    try:
        if CURSOR_FILE.exists():
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k in ("prio_1", "prio_2", "prio_3"):
                    if k in data:
                        cursores[k] = max(base, int(data[k]))
                    elif "ultimo_id" in data:
                        cursores[k] = max(base, int(data["ultimo_id"]))
    except Exception:
        pass
    return cursores


def guardar_cursores(cursores: Dict[str, int]):
    """Persiste los cursores por nivel de prioridad."""
    try:
        CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "prio_1": cursores.get("prio_1", 0),
            "prio_2": cursores.get("prio_2", 0),
            "prio_3": cursores.get("prio_3", 0),
            "ultimo_id": max(cursores.values()) if cursores else 0,
            "actualizado_el": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(CURSOR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def alimentar_enacom(
    batch_size: int = 5000,
    target_pc: str = "PC-08",
    limit_total: Optional[int] = None,
    dry_run: bool = False,
    meses: int = 2,
    fecha_desde: Optional[str] = None,
    conn: Optional[Any] = None,
    silent: bool = False
) -> int:
    start_time = time.time()
    ahora = datetime.now()
    if not fecha_desde:
        fecha_desde = calcular_fecha_desde(meses)

    if not silent:
        print("=" * 80)
        print("ALIMENTADOR COLA AUTOMATIZACIÓN: Cobro Express -> ENACOM Web")
        print(f"Fecha/Hora inicio:       {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Ventana temporal:        Últimos {meses} meses (>= {fecha_desde})")
        print(f"Tamaño de lote (batch):  {batch_size:,} registros")
        print(f"Target PC destino:       {target_pc}")
        print(f"Límite total a insertar: {f'{limit_total:,}' if limit_total else 'Sin límite (todos los elegibles)'}")
        print(f"Estrategia de Prioridad: Cascada P1 (AMBA/Mza) -> P2 (Sur/Cuyo) -> P3 (Resto)")
        print(f"Modo DRY-RUN:            {'SÍ (Simulación sin escrituras)' if dry_run else 'NO (Producción activa)'}")
        print("=" * 80)

    owns_conn = False
    if conn is None:
        conn = conectar_bd()
        owns_conn = True

    # 1. Determinar ID base de la ventana
    if not silent:
        print(f"\n🔍 Calculando ID inicial para la ventana desde {fecha_desde[:10]}...")
    min_id = obtener_id_minimo_mes_vigente(conn, fecha_desde)
    if not silent:
        print(f"   -> ID mínimo detectado para {fecha_desde[:7]}: {min_id:,}")

    # Query de inserción atómica (nota: paso_por_iris es columna generada automáticamente por MySQL)
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
            scrapers_intentados
        ) VALUES (
            %s, %s, %s, %s, NULL, %s, NULL, NULL, 0, NOW(), NULL, NULL, %s, %s, %s, NOW(), %s
        );
    """

    total_leidos = 0
    total_insertados = 0
    total_ignorados_duplicados = 0
    lote_num = 0
    cursores = obtener_cursores_guardados(min_id)

    cur_read = conn.cursor(dictionary=True)
    cur_write = conn.cursor()

    niveles_prioridad = [
        (1, "Prioridad 1 (AMBA y Mendoza)", SQL_FILTRO_P1, "prio_1"),
        (2, "Prioridad 2 (Sur y Cuyo)", SQL_FILTRO_P2, "prio_2"),
        (3, "Prioridad 3 (Resto del país)", SQL_FILTRO_P3, "prio_3")
    ]

    try:
        for prio_num, prio_nombre, filtro_prio, cursor_key in niveles_prioridad:
            if limit_total and total_insertados >= limit_total:
                break

            cursor_actual = cursores.get(cursor_key, min_id - 1)
            if not silent:
                print(f"\n🎯 [BUSCANDO {prio_nombre}] Cursor inicial: {cursor_actual:,}", flush=True)

            while True:
                # Calcular cuánto pedir en este lote
                if limit_total:
                    restante = limit_total - total_insertados
                    if restante <= 0:
                        break
                    pedir_lote = min(batch_size, restante)
                else:
                    pedir_lote = batch_size

                t0_batch = time.time()
                query_select = f"""
                    SELECT c.id, c.numero_de_linea, c.dni, c.prioridad, c.target_pc,
                           c.scrapers_intentados, c.paso_por_iris, c.payload
                    FROM cola_automatizacion c FORCE INDEX (PRIMARY)
                    WHERE c.id > %s
                      AND c.auto_id = 'scraper_cobro_express'
                      AND c.estado = 'completado'
                      AND c.fecha_fin >= %s
                      AND {filtro_prio}
                      AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.claro.status')) = 'sin_coincidencia'
                      AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.personal.status')) = 'sin_coincidencia'
                      AND JSON_UNQUOTE(JSON_EXTRACT(c.resultado, '$.movistar.status')) = 'sin_coincidencia'
                    LIMIT %s;
                """
                cur_read.execute(query_select, (cursor_actual, fecha_desde, pedir_lote))
                filas = cur_read.fetchall()

                if not filas:
                    if not silent:
                        print(f"   [FIN NIVEL] No hay más registros en {prio_nombre}. Avanzando a la siguiente prioridad si resta cupo.", flush=True)
                    break

                lote_num += 1
                total_leidos += len(filas)
                cursor_actual = filas[-1]["id"]
                cursores[cursor_key] = cursor_actual

                buffer_insert = []
                for r in filas:
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

                    dni_val = str(r.get("dni") or pay_dict.get("dni") or "").strip()
                    dni_limpio = "".join(filter(str.isdigit, dni_val)) if dni_val else None

                    payload_str = json.dumps(pay_dict, ensure_ascii=False) if pay_dict else json.dumps(r.get("payload") or {}, ensure_ascii=False)
                    paso_iris = bool(r.get("paso_por_iris"))
                    scrapers_str = "iris_scraper,scraper_cobro_express" if paso_iris else "scraper_cobro_express"

                    record = (
                        prio_num,
                        target_pc,
                        "pendiente",
                        "scraper_enacom",
                        payload_str,
                        None,  # dias_desde_ultimo_cambio = NULL
                        num_linea,
                        dni_limpio,
                        scrapers_str
                    )
                    buffer_insert.append(record)

                if not dry_run:
                    cur_write.executemany(query_insert, buffer_insert)
                    conn.commit()
                    insertadas_este_lote = cur_write.rowcount
                    ignoradas_este_lote = len(buffer_insert) - insertadas_este_lote
                    guardar_cursores(cursores)
                else:
                    insertadas_este_lote = len(buffer_insert)
                    ignoradas_este_lote = 0

                total_insertados += insertadas_este_lote
                total_ignorados_duplicados += ignoradas_este_lote
                t_lote = round(time.time() - t0_batch, 2)

                if not silent:
                    print(
                        f"   [{prio_nombre} Lote #{lote_num:03d}] Leídos: {len(filas):,d} | "
                        f"Insertados: {insertadas_este_lote:,d} | "
                        f"Ignorados/Dupl: {ignoradas_este_lote:,d} | "
                        f"Último ID: {cursor_actual} | "
                        f"Tiempo: {t_lote}s",
                        flush=True
                    )

                if len(filas) < pedir_lote:
                    if not silent:
                        print(f"   [FIN NIVEL] Alcanzado límite disponible en {prio_nombre}.", flush=True)
                    break



    except Exception as e:
        if owns_conn:
            try:
                conn.rollback()
            except Exception:
                pass
        if not silent:
            print(f"\n[ERROR CRÍTICO] Falló el procesamiento del lote #{lote_num}: {e}")
        raise
    finally:
        try:
            cur_read.close()
        except Exception:
            pass
        try:
            cur_write.close()
        except Exception:
            pass
        if owns_conn and conn:
            try:
                conn.close()
            except Exception:
                pass

    elapsed = round(time.time() - start_time, 2)
    rate = round(total_insertados / elapsed, 1) if elapsed > 0 else 0

    if not silent:
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

    return total_insertados


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alimentador de cola_automatizacion para scraper_enacom desde Cobro Express.")
    parser.add_argument("--batch-size", type=int, default=5000, help="Tamaño de lote para el INSERT (default: 5000)")
    parser.add_argument("--target-pc", type=str, default="PC-08", help="Target PC asignado (default: PC-08)")
    parser.add_argument("--limit", type=int, default=None, help="Límite máximo total de registros a transferir")
    parser.add_argument("--dry-run", action="store_true", help="Simula la operación sin escribir en MySQL")
    parser.add_argument("--meses", type=int, default=2, help="Cantidad de meses vigentes a incluir hacia atrás (default: 2, ej: septiembre y octubre)")
    parser.add_argument("--fecha-desde", type=str, default=None, help="Fecha mínima exacta YYYY-MM-DD (default: calcula los últimos N meses)")

    args = parser.parse_args()

    alimentar_enacom(
        batch_size=args.batch_size,
        target_pc=args.target_pc,
        limit_total=args.limit,
        dry_run=args.dry_run,
        meses=args.meses,
        fecha_desde=args.fecha_desde
    )
