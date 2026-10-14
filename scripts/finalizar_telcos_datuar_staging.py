# -*- coding: utf-8 -*-
"""
Script operativo: Finalizar registros Telcos atascados en 'datuar'/'pendiente' (cola_automatizacion).

Contexto:
    Antes del fix de ReglaPipeline.CADENA_COLA_AUTOMATIZACION, las líneas de cola_automatizacion
    con DNI procesadas por Telcos se derivaban localmente a 'datuar' (estado_local='pendiente').
    Como en cola_automatizacion no existe consumidor Datuar, nunca pasaban a 'listo_para_subir'
    y el push semaforizado de 5.000 jamás las tomaba.

Acción:
    UPDATE tareas_staging SET scraper_actual='finalizado', estado_local='listo_para_subir'
    WHERE tipo_cola='cola_automatizacion' AND scraper_actual='datuar' AND estado_local='pendiente'
      [AND auto_id = --auto-id]

Uso:
    python scripts/finalizar_telcos_datuar_staging.py --auto-id ce_bases_produccion [--dry-run]
    python supervisor_vps.py --queue cola_automatizacion --auto-id ce_bases_produccion --sync-now
"""
import sys
import sqlite3
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Finaliza registros Telcos derivados a Datuar en cola_automatizacion.")
    parser.add_argument("--auto-id", type=str, default=None, help="Filtrar por auto_id (ej: ce_bases_produccion)")
    parser.add_argument("--dry-run", action="store_true", help="Solo contar, sin modificar")
    args = parser.parse_args()

    db_path = getattr(config, "LOCAL_STAGING_DB_PATH", str(PROJECT_ROOT / "data" / "staging_local.db"))
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        where = "tipo_cola = 'cola_automatizacion' AND scraper_actual = 'datuar' AND estado_local = 'pendiente'"
        params = []
        if args.auto_id:
            where += " AND auto_id = ?"
            params.append(args.auto_id)

        total = conn.execute(f"SELECT COUNT(*) FROM tareas_staging WHERE {where}", params).fetchone()[0]
        print(f"Registros candidatos: {total:,}")
        if args.dry_run or total == 0:
            return

        with conn:
            cur = conn.execute(
                f"UPDATE tareas_staging SET scraper_actual = 'finalizado', estado_local = 'listo_para_subir' WHERE {where}",
                params
            )
        print(f"Registros pasados a 'listo_para_subir': {cur.rowcount:,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
