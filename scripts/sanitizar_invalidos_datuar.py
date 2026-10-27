# -*- coding: utf-8 -*-
"""
Script de Sanitización Atómica Masiva: Formatos Inválidos Datuar
Pasa de 1 solo paso a 'completado' con 'sin_coincidencia' todos los registros
pendientes o en_proceso de Datuar que carezcan de DNI o pertenezcan a personas
jurídicas (CUIT 30/33/34), tanto en el VPS como en Staging local.
"""
import os
import sys
import json
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, ".")
load_dotenv()
import config


def sanitizar_vps() -> int:
    import mysql.connector

    conn = mysql.connector.connect(
        host=os.getenv("VPS_DBHOST", config.VPS_DBHOST),
        port=int(os.getenv("VPS_DBPORT", config.VPS_DBPORT)),
        user=os.getenv("COLA_AUTO_DBUSER", "automatizaciones"),
        password=os.getenv("COLA_AUTO_DBPASS", ""),
        database=os.getenv("VPS_DBNAME", config.VPS_DBNAME),
        connection_timeout=20,
        use_pure=True
    )
    cursor = conn.cursor()

    query = """
        UPDATE cola_automatizacion
        SET estado = 'completado',
            resultado = %s,
            scrapers_intentados = CASE 
                WHEN scrapers_intentados IS NULL OR scrapers_intentados = '' THEN 'scraper_datuar'
                WHEN FIND_IN_SET('scraper_datuar', scrapers_intentados) > 0 THEN scrapers_intentados
                ELSE CONCAT(scrapers_intentados, ',scraper_datuar')
            END,
            fecha_fin = NOW()
        WHERE auto_id = 'scraper_datuar'
          AND estado IN ('pendiente', 'en_proceso')
          AND (
              (dni IS NULL OR dni = '' OR dni = '0')
              OR ((dni LIKE '30%' OR dni LIKE '33%' OR dni LIKE '34%') AND LENGTH(dni) = 11)
          );
    """
    payload = json.dumps({"status": "sin_coincidencia", "motivo": "formato_invalido_omitido"}, ensure_ascii=False)
    cursor.execute(query, (payload,))
    afectados = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return afectados


def sanitizar_staging_local() -> int:
    db_path = Path("data/staging_local.db")
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    query = """
        UPDATE tareas_staging
        SET estado_local = 'sincronizado',
            resultado_json = :res_json,
            scraper_actual = 'finalizado',
            descripcion = 'Formato invalido omitido (sin DNI o empresa)',
            fecha_sincronizado = CURRENT_TIMESTAMP
        WHERE estado_local IN ('pendiente', 'listo_para_subir')
          AND (
              (dni IS NULL OR dni = '' OR dni = '0')
              OR ((dni LIKE '30%' OR dni LIKE '33%' OR dni LIKE '34%') AND LENGTH(dni) = 11)
          );
    """
    res_str = json.dumps({"status": "sin_coincidencia", "motivo": "formato_invalido_omitido"}, ensure_ascii=False)
    cursor.execute(query, {"res_json": res_str})
    afectados = cursor.rowcount
    conn.commit()
    conn.close()
    return afectados


def main():
    print("[1/2] Sanitizando cola en VPS MySQL (cola_automatizacion)...")
    vps_afectados = sanitizar_vps()
    print(f"      -> {vps_afectados:,} registros actualizados a 'completado' sin coincidencia en VPS.")

    print("[2/2] Sanitizando SQLite Staging local (tareas_staging)...")
    local_afectados = sanitizar_staging_local()
    print(f"      -> {local_afectados:,} registros descartados/sincronizados en SQLite local.")

    print("\n[OK] Sanitización completada con éxito.")


if __name__ == "__main__":
    main()
