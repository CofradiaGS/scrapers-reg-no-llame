# -*- coding: utf-8 -*-
"""
Script para ingestar líneas desde el CSV de bases adquiridas del escritorio
hacia cola_automatizacion para scraper_enacom con:
- prioridad: 1
- fuente: bases_adquiridas_mov_para_scrap
- target_pc: PC-08
- auto_id: scraper_enacom
- estado: pendiente
"""
import os
import sys
import time
import json
import csv
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector

# UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

CSV_PATH = Path(os.path.expanduser("~/Desktop/bases_adquiridas_mov_20261103.csv"))

def conectar_bd():
    return mysql.connector.connect(
        host=os.getenv("VPS_DBHOST", "172.16.20.15"),
        user=os.getenv("COLA_AUTO_DBUSER", "automatizaciones"),
        password=os.getenv("COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
        database=os.getenv("VPS_DBNAME", "bases"),
        port=int(os.getenv("VPS_DBPORT", 3306)),
        use_pure=True,
        autocommit=False,
        connection_timeout=60
    )

def ingestar_archivo(csv_path: Path, target_pc: str = "PC-08", batch_size: int = 5000):
    if not csv_path.exists():
        print(f"ERROR: No se encuentra el archivo en {csv_path}")
        return

    print("=" * 80)
    print("INGESTA BASES ADQUIRIDAS -> COLA AUTOMATIZACION (ENACOM)")
    print(f"Archivo origen:  {csv_path}")
    print(f"Target PC:       {target_pc}")
    print(f"Prioridad:       1")
    print(f"Fuente:          bases_adquiridas_mov_para_scrap")
    print(f"Auto ID:         scraper_enacom")
    print(f"Tamaño de lote:  {batch_size:,} registros")
    print("=" * 80, flush=True)

    conn = conectar_bd()
    cur = conn.cursor()

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
            fuente
        ) VALUES (
            %s, %s, %s, %s, NULL, %s, NULL, NULL, 0, NOW(), NULL, NULL, NULL, %s, NULL, NOW(), NULL, %s
        );
    """

    total_leidos = 0
    total_insertados = 0
    total_duplicados = 0
    buffer = []
    t0 = time.time()
    lote_num = 0

    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        # Identificar columna ani
        col_idx = 0
        if header:
            for idx, h in enumerate(header):
                if "ani" in h.lower() or "linea" in h.lower() or "numero" in h.lower():
                    col_idx = idx
                    break

        for row in reader:
            if not row or len(row) <= col_idx:
                continue
            raw_val = row[col_idx].strip()
            digits = "".join(filter(str.isdigit, raw_val))
            if len(digits) == 10:
                ani = digits
            elif len(digits) > 10 and digits.startswith("54"):
                ani = digits[2:]
            elif len(digits) > 10 and digits.startswith("0"):
                ani = digits.lstrip("0")
            else:
                continue

            if len(ani) != 10:
                continue

            total_leidos += 1
            payload_str = json.dumps({"ani": ani, "numero_de_linea": ani}, ensure_ascii=False)
            fuente_str = "bases_adquiridas_mov_para_scrap"

            buffer.append((
                1,                # prioridad = 1
                target_pc,        # target_pc = PC-08
                "pendiente",      # estado
                "scraper_enacom", # auto_id
                payload_str,
                ani,              # numero_de_linea
                fuente_str        # fuente
            ))

            if len(buffer) >= batch_size:
                lote_num += 1
                t_lote = time.time()
                cur.executemany(query_insert, buffer)
                conn.commit()
                ins = cur.rowcount
                dup = len(buffer) - ins
                total_insertados += ins
                total_duplicados += dup
                print(
                    f"   [Lote #{lote_num:03d}] Leídos: {total_leidos:,} | "
                    f"Insertados: {ins:,} | Ignorados/Dupl: {dup:,} | "
                    f"Tiempo lote: {time.time() - t_lote:.2f}s",
                    flush=True
                )
                buffer.clear()

        # Último lote remanente
        if buffer:
            lote_num += 1
            t_lote = time.time()
            cur.executemany(query_insert, buffer)
            conn.commit()
            ins = cur.rowcount
            dup = len(buffer) - ins
            total_insertados += ins
            total_duplicados += dup
            print(
                f"   [Lote #{lote_num:03d} FINAL] Leídos: {total_leidos:,} | "
                f"Insertados: {ins:,} | Ignorados/Dupl: {dup:,} | "
                f"Tiempo lote: {time.time() - t_lote:.2f}s",
                flush=True
            )
            buffer.clear()

    cur.close()
    conn.close()

    total_tiempo = time.time() - t0
    print("=" * 80)
    print("RESUMEN DE INGESTA")
    print(f"Total líneas válidas leídas: {total_leidos:,}")
    print(f"Nuevas tareas insertadas:    {total_insertados:,}")
    print(f"Ignoradas (ya existían):     {total_duplicados:,}")
    print(f"Tiempo total:                {total_tiempo:.2f}s ({total_leidos/max(1, total_tiempo):.1f} reg/s)")
    print("=" * 80, flush=True)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "PC-08"
    ingestar_archivo(CSV_PATH, target_pc=target)

