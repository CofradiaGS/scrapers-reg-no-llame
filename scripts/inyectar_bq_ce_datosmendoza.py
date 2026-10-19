# -*- coding: utf-8 -*-
"""
Script de Inyección: BigQuery a MySQL VPS (cola_automatizacion)
Sección destino: `auto_id = 'ce_datosmendoza'`
Target PC: Abierto (NULL) para cualquier nodo worker

Lee de BigQuery:
- ani
- dni
- nombre
- fuente_base_adicional
- evento
- ce_nombre_empresa
de la tabla `dw_movistar_mart.fct_scrap__bases_produccion`
con filtro `fuente_base_adicional = 'datos_mendoza_20261010'`.
"""
import os
import sys
import re
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from google.cloud import bigquery
from google.oauth2 import service_account
import mysql.connector

import config

AUTO_ID = "ce_datosmendoza"
TARGET_PC = None  # Abierto para cualquier worker
BATCH_SIZE = 2000
CRED_PATH = PROJECT_ROOT / "iacuna-service-account-or.json"

PREFIJOS_P1 = ("11", "261")
PREFIJOS_P2 = ("2901", "2920", "294", "2945", "2966", "297", "298", "299", "280", "2954", "2302", "2931", "2934", "2942")


def determinar_prioridad(ani: str) -> int:
    """Prioridad 1 para Mendoza (261) y AMBA (11), 2 Patagonia, 3 Resto."""
    if any(ani.startswith(p) for p in PREFIJOS_P1):
        return 1
    if any(ani.startswith(p) for p in PREFIJOS_P2):
        return 2
    return 3


def normalizar_linea(tel_str: str) -> Optional[str]:
    """Extrae y normaliza el número telefónico a exactamente 10 dígitos."""
    if not tel_str:
        return None
    digits = re.sub(r"\D", "", str(tel_str).strip())
    if digits.startswith("549"):
        digits = digits[3:]
    elif digits.startswith("54"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits

    # Quitar 15 si tiene prefijo
    for p_len in (2, 3, 4):
        if len(digits) == 12 and digits[p_len:p_len+2] == "15":
            cand = digits[:p_len] + digits[p_len+2:]
            if len(cand) == 10:
                return cand

    return None


def limpiar_dni(dni_raw: Any) -> Optional[str]:
    """Limpia el DNI dejando solo dígitos si es válido."""
    if not dni_raw:
        return None
    d = re.sub(r"\D", "", str(dni_raw).strip())
    return d if (6 <= len(d) <= 11) else None


def conectar_mysql():
    """Conecta a MySQL VPS central."""
    return mysql.connector.connect(
        host=config.VPS_DBHOST,
        user=getattr(config, "COLA_AUTO_DBUSER", "automatizaciones"),
        password=getattr(config, "COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
        database=config.VPS_DBNAME,
        port=config.VPS_DBPORT,
        autocommit=False,
        connection_timeout=30
    )


def ejecutar_extraccion_e_inyeccion():
    print("=" * 70)
    print("EXTRACCION BIGQUERY -> INYECCION A COLA_AUTOMATIZACION")
    print(f"Sección destino (`auto_id`): {AUTO_ID}")
    print(f"Target PC asignado:          {TARGET_PC or 'ABIERTO (Cualquier worker)'}")
    print(f"Credenciales GCP:            {CRED_PATH.name}")
    print("=" * 70)

    if not CRED_PATH.exists():
        raise FileNotFoundError(f"No se encontró el archivo de credenciales: {CRED_PATH}")

    # 1. Conexión a BigQuery
    credentials = service_account.Credentials.from_service_account_file(str(CRED_PATH))
    bq_client = bigquery.Client(credentials=credentials, project=credentials.project_id)

    query = """
    SELECT 
      ani, 
      dni, 
      nombre, 
      fuente_base_adicional, 
      evento, 
      ce_nombre_empresa
    FROM `dw_movistar_mart.fct_scrap__bases_produccion`
    WHERE fuente_base_adicional = 'datos_mendoza_20261010'
    """

    print("📡 Ejecutando consulta en BigQuery...")
    t0 = time.time()
    query_job = bq_client.query(query)
    rows = query_job.result()
    duracion_bq = time.time() - t0
    print(f"✅ Descarga completada desde BigQuery en {duracion_bq:.2f}s.")

    # 2. Conectar a MySQL VPS
    conn = conectar_mysql()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM cola_automatizacion WHERE auto_id = %s", (AUTO_ID,))
    prev_count = cur.fetchone()[0]
    print(f"ℹ️ Registros existentes previos en '{AUTO_ID}': {prev_count:,}")

    query_insert = """
        INSERT IGNORE INTO cola_automatizacion (
            prioridad,
            target_pc,
            estado,
            auto_id,
            payload,
            numero_de_linea,
            dni
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s
        )
    """

    total_leidas = 0
    total_insertadas = 0
    total_duplicadas = 0
    total_invalidas = 0
    vistas_ani = set()
    buffer_insert = []

    print("\n🚀 Procesando filas e insertando en lotes a MySQL VPS...")
    t_start_insert = time.time()

    for row in rows:
        total_leidas += 1

        ani_raw = row.get("ani")
        ani = normalizar_linea(ani_raw)
        if not ani or len(ani) != 10:
            total_invalidas += 1
            continue

        if ani in vistas_ani:
            total_duplicadas += 1
            continue
        vistas_ani.add(ani)

        dni_limpio = limpiar_dni(row.get("dni"))
        nombre = str(row.get("nombre") or "").strip()
        fuente = str(row.get("fuente_base_adicional") or "").strip()
        evento = str(row.get("evento") or "").strip()
        ce_nombre_empresa = str(row.get("ce_nombre_empresa") or "").strip()

        prio = determinar_prioridad(ani)

        payload_dict = {
            "numero_de_linea": ani,
            "dni": dni_limpio,
            "nombre": nombre,
            "fuente_base_adicional": fuente,
            "evento": evento,
            "ce_nombre_empresa_previo": ce_nombre_empresa
        }
        payload_json = json.dumps(payload_dict, ensure_ascii=False)

        record = (
            prio,
            TARGET_PC,
            "pendiente",
            AUTO_ID,
            payload_json,
            ani,
            dni_limpio
        )
        buffer_insert.append(record)

        if len(buffer_insert) >= BATCH_SIZE:
            cur.executemany(query_insert, buffer_insert)
            conn.commit()
            total_insertadas += cur.rowcount
            buffer_insert.clear()
            print(f"  • Insertados acumulados: {total_insertadas:,} (Leídos BQ: {total_leidas:,})")

    # Flush final
    if buffer_insert:
        cur.executemany(query_insert, buffer_insert)
        conn.commit()
        total_insertadas += cur.rowcount
        buffer_insert.clear()

    cur.execute("SELECT COUNT(*) FROM cola_automatizacion WHERE auto_id = %s", (AUTO_ID,))
    final_count = cur.fetchone()[0]
    conn.close()

    duracion_total = time.time() - t_start_insert
    print("\n" + "=" * 70)
    print("RESUMEN DE INYECCIÓN")
    print("=" * 70)
    print(f"Total filas leídas de BigQuery:      {total_leidas:,}")
    print(f"Total líneas válidas únicas:         {len(vistas_ani):,}")
    print(f"Total líneas inválidas descartadas:  {total_invalidas:,}")
    print(f"Total duplicadas en lote BQ:         {total_duplicadas:,}")
    print(f"Nuevos registros insertados en VPS:  {total_insertadas:,}")
    print(f"Total registros finales en {AUTO_ID}: {final_count:,}")
    print(f"Tiempo de inserción:                 {duracion_total:.2f}s")
    print("=" * 70)


if __name__ == "__main__":
    ejecutar_extraccion_e_inyeccion()
