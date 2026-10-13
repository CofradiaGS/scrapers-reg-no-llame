# -*- coding: utf-8 -*-
"""
Script de Reparación y Retroalimentación: Enriquecimiento BCRA para Registros Completados sin CUIT
Recupera los registros previamente completados que no obtuvieron CUIT en CuitOnline/Datuar,
calcula su CUIL algorítmico oficial (ANSES Módulo 11) y consulta la Central de Deudores del BCRA,
actualizando situación crediticia, deudas con Banco Macro y entidades financieras sin alterar
las líneas telefónicas ya descubiertas en IRIS o auditadas en Telcos.
"""
import sys
import time
import json
import sqlite3
import logging
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.domain.cuit_validator import obtener_cuils_candidatos
from adapters.scrapers.bcra.bcra_client import BCRAClient

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ReparadorBCRA")


def reparar_bcra():
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT dni, nombre_excel, nombre_oficial, genero, datos_json
        FROM personas
        WHERE estado_proceso = 'completado' AND (cuit IS NULL OR cuit = '')
    """)
    filas = cursor.fetchall()
    total = len(filas)
    logger.info(f"Iniciando reparación BCRA para {total} registros completados sin CUIT...")

    if total == 0:
        logger.info("No hay registros pendientes de reparación en estado completado.")
        conn.close()
        return

    bcra = BCRAClient(timeout=12)
    reparados = 0
    con_deuda_macro = 0
    con_deuda_sistema = 0

    try:
        for idx, row in enumerate(filas, 1):
            dni = row["dni"]
            nombre_excel = row["nombre_excel"] or ""
            nombre_existente = row["nombre_oficial"] or ""
            genero_existente = row["genero"] or ""
            datos_raw = row["datos_json"]
            datos_json = json.loads(datos_raw) if datos_raw else {}

            cuil_1, cuil_2 = obtener_cuils_candidatos(dni, nombre_excel, genero_existente)
            
            # 1. Probar candidato primario
            cuit_definitivo = cuil_1
            bcra_data = bcra.consultar_deuda(cuil_1)
            time.sleep(0.08)

            if bcra_data and (not bcra_data.get("sin_deuda") or bcra_data.get("denominacion")):
                cuit_definitivo = cuil_1
            else:
                # 2. Probar candidato alternativo
                bcra_data_alt = bcra.consultar_deuda(cuil_2)
                time.sleep(0.08)
                if bcra_data_alt and (not bcra_data_alt.get("sin_deuda") or bcra_data_alt.get("denominacion")):
                    cuit_definitivo = cuil_2
                    bcra_data = bcra_data_alt
                else:
                    cuit_definitivo = cuil_1
                    bcra_data = bcra_data or bcra_data_alt or {
                        "cuit": cuit_definitivo,
                        "sin_deuda": True,
                        "entidades": [],
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_situacion": None
                    }

            # Extracción de campos normalizados
            denominacion_bcra = bcra_data.get("denominacion", "").strip()
            nombre_final = nombre_existente or denominacion_bcra or nombre_excel

            if not genero_existente:
                if cuit_definitivo.startswith("20"):
                    genero_final = "Masculino"
                elif cuit_definitivo.startswith("27"):
                    genero_final = "Femenino"
                elif cuit_definitivo.startswith("23") and cuit_definitivo.endswith("4"):
                    genero_final = "Femenino"
                elif cuit_definitivo.startswith("23") and cuit_definitivo.endswith("9"):
                    genero_final = "Masculino"
                else:
                    genero_final = ""
            else:
                genero_final = genero_existente

            peor_sit = bcra_data.get("peor_situacion", 0)
            deuda_total = bcra_data.get("deuda_total_miles", 0.0)
            deuda_macro = bcra_data.get("deuda_macro_miles", 0.0)
            sit_macro = bcra_data.get("deuda_macro_situacion")

            if deuda_total > 0:
                con_deuda_sistema += 1
            if deuda_macro > 0:
                con_deuda_macro += 1

            # Actualizar payload acumulativo
            datos_json["bcra"] = bcra_data
            datos_json["cuil_determinado"] = cuit_definitivo
            datos_json_str = json.dumps(datos_json, ensure_ascii=False)

            # Persistencia en BD
            cursor.execute("""
                UPDATE personas
                SET cuit = ?,
                    nombre_oficial = COALESCE(NULLIF(?, ''), nombre_oficial),
                    genero = COALESCE(NULLIF(?, ''), genero),
                    peor_situacion_bcra = ?,
                    deuda_macro_miles = ?,
                    deuda_macro_situacion = ?,
                    deuda_total_bcra_miles = ?,
                    datos_json = ?,
                    fecha_actualizacion = datetime('now')
                WHERE dni = ?
            """, (
                cuit_definitivo, nombre_final, genero_final,
                peor_sit, deuda_macro, sit_macro, deuda_total,
                datos_json_str, dni
            ))

            # Reemplazar entidades BCRA
            cursor.execute("DELETE FROM bcra_entidades WHERE dni = ?", (dni,))
            for ent in bcra_data.get("entidades", []):
                cursor.execute("""
                    INSERT INTO bcra_entidades (dni, cuit, entidad, situacion, monto_miles, periodo)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    dni, cuit_definitivo, ent.get("entidad", ""), ent.get("situacion", 1),
                    ent.get("monto_miles", 0.0), ent.get("periodo", "")
                ))

            conn.commit()
            reparados += 1

            if idx % 25 == 0 or idx == total:
                logger.info(f"Progreso: [{idx}/{total}] ({(idx/total)*100:.1f}%) | "
                            f"Con Deuda BCRA: {con_deuda_sistema} | Con Deuda Macro: {con_deuda_macro}")

    except KeyboardInterrupt:
        logger.warning("\nProceso interrumpido por el usuario. Cambios consolidados hasta el último registro.")
    finally:
        conn.close()
        logger.info(f"\nResumen Final de Reparación:")
        logger.info(f" - Registros reparados: {reparados}/{total}")
        logger.info(f" - Con Deuda en BCRA: {con_deuda_sistema}")
        logger.info(f" - Con Deuda directa en Banco Macro: {con_deuda_macro}")


if __name__ == "__main__":
    reparar_bcra()
