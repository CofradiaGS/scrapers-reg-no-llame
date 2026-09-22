# -*- coding: utf-8 -*-
"""
Script de Auditoría y Re-Verificación: Muestra de Registros 'Sin Coincidencia' en IRIS HTTP

Este script selecciona una muestra de N registros (default 2000) de la tabla `queue_registro_no_llame`
que hayan sido clasificados previamente como `sin_coincidencia` en IRIS, y vuelve a consultar la API HTTP
de IRIS de forma atómica para validar si existieron falsos negativos producto de la velocidad/concurrencia.

Uso:
    python scripts/auditar_no_coincidencias_iris.py --sample 2000
    python scripts/auditar_no_coincidencias_iris.py --sample 500 --dry-run
"""
import os
import sys
import time
import json
import argparse
import logging
from typing import List, Dict, Any

# Asegurar path raíz del proyecto
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from core.domain.entities import Linea, StatusScraping

# Configure encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Configuración de logging para consola
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AuditoriaIris")


def obtener_muestra_sin_coincidencia(queue_adapter: MySQLQueueAdapter, limit: int = 2000) -> List[Dict[str, Any]]:
    """Extrae N registros que tengan estado sin_coincidencia en IRIS."""
    conn = queue_adapter._get_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        # Obtener el id máximo para restringir el rango de búsqueda en la clave primaria
        cursor.execute(f"SELECT MAX(id) AS max_id FROM `{config.VPS_DB_TABLE}`")
        row = cursor.fetchone()
        max_id = row["max_id"] if row and row["max_id"] else 0
        min_id = max(0, max_id - 200000)

        query = f"""
            SELECT id, ani, estado, scraper_actual, fuente, datos_json
            FROM `{config.VPS_DB_TABLE}`
            WHERE id >= %s
              AND datos_json LIKE '%"sin_coincidencia"%'
            ORDER BY id DESC
            LIMIT %s
        """
        cursor.execute(query, (min_id, limit))
        filas = cursor.fetchall()
        return filas
    finally:
        cursor.close()
        conn.close()


def actualizar_registro_rescatado(queue_adapter: MySQLQueueAdapter, item_rescatado: Dict[str, Any]) -> None:
    """Actualiza en MySQL un registro que resultó en falso negativo (nueva coincidencia)."""
    conn = queue_adapter._get_connection()
    cursor = conn.cursor()
    try:
        reg_id = item_rescatado["id"]
        datos_json = json.dumps(item_rescatado["datos"], ensure_ascii=False)
        desc = item_rescatado["descripcion"][:195]

        query = f"""
            UPDATE `{config.VPS_DB_TABLE}`
            SET estado = 'completado',
                scraper_actual = 'finalizado',
                descripcion_scraper = %s,
                datos_json = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """
        cursor.execute(query, (desc, datos_json, reg_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error actualizando registro rescatado ID {item_rescatado.get('id')}: {e}")
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Auditoría de Re-Verificación de Sin Coincidencias IRIS")
    parser.add_argument("--sample", type=int, default=2000, help="Cantidad de registros a auditar (default: 2000)")
    parser.add_argument("--dry-run", action="store_true", help="Si se activa, no guarda cambios en la DB")
    parser.add_argument("--delay", type=float, default=0.2, help="Pausa en segundos entre consultas (default: 0.2s)")
    args = parser.parse_args()

    print("=" * 72)
    print("🔍 AUDITORÍA DE RE-VERIFICACIÓN DE 'SIN COINCIDENCIA' IRIS HTTP")
    print(f"  • Muestra objetivo: {args.sample:,} registros")
    print(f"  • Modo Dry-Run:     {'SÍ (Solo Lectura)' if args.dry_run else 'NO (Auto-corregirá falsos negativos)'}")
    print(f"  • Delay entre req:  {args.delay}s")
    print("=" * 72)

    queue_adapter = MySQLQueueAdapter()

    logger.info("Obteniendo muestra de registros sin coincidencia desde MySQL VPS...")
    muestra = obtener_muestra_sin_coincidencia(queue_adapter, limit=args.sample)

    if not muestra:
        logger.warning("No se encontraron registros con 'iris.status == sin_coincidencia' en la base de datos.")
        return

    logger.info(f"✅ Muestra obtenida exitosamente: {len(muestra):,} registros.")

    # Inicializar motor IRIS HTTP
    logger.info("Inicializando y autenticando motor IRIS HTTP...")
    scraper = IrisHttpAdapter()
    scraper.iniciar()

    if not scraper.autenticar():
        logger.error("❌ Fallo en la autenticación con IRIS. Abortando auditoría.")
        return

    logger.info("✅ Sesión IRIS iniciada. Comenzando auditoría de re-verificación...")

    stats = {
        "total": len(muestra),
        "confirmados_sin_coincidencia": 0,
        "falsos_negativos_rescatados": 0,
        "errores_http": 0,
        "t0": time.time()
    }

    rescatados = []

    for idx, reg in enumerate(muestra, start=1):
        reg_id = reg["id"]
        ani = str(reg["ani"])

        try:
            linea = Linea(ani=ani)
            resultado = scraper.consultar_linea(linea)

            if resultado.status == StatusScraping.COINCIDENCIA:
                stats["falsos_negativos_rescatados"] += 1
                titular_nom = f"{resultado.titular.nombre} {resultado.titular.apellido}".strip() if resultado.titular else "Desconocido"
                doc = resultado.titular.nro_documento if resultado.titular else ""
                msg = f"🎯 [FALSO NEGATIVO RESCATADO!] ID:{reg_id} | ANI:{ani} -> Titular: {titular_nom} (Doc: {doc})"
                logger.warning(msg)

                # Preparar fusión de datos
                datos_previos = json.loads(reg["datos_json"]) if isinstance(reg["datos_json"], str) else (reg["datos_json"] or {})
                datos_acumulados = dict(datos_previos)
                datos_acumulados.update(resultado.to_namespace_dict())

                item_rescatado = {
                    "id": reg_id,
                    "ani": ani,
                    "descripcion": resultado.descripcion or f"Rescatado por Auditoría IRIS",
                    "datos": datos_acumulados
                }
                rescatados.append(item_rescatado)

                if not args.dry_run:
                    actualizar_registro_rescatado(queue_adapter, item_rescatado)

            elif resultado.status == StatusScraping.SIN_COINCIDENCIA:
                stats["confirmados_sin_coincidencia"] += 1
            else:
                stats["errores_http"] += 1

        except Exception as e:
            stats["errores_http"] += 1
            logger.error(f"Error al re-consultar ID:{reg_id} ANI:{ani}: {e}")

        # Progreso acumulativo cada 50 registros
        if idx % 50 == 0 or idx == len(muestra):
            elapsed = time.time() - stats["t0"]
            rpm = round((idx / elapsed) * 60, 1) if elapsed > 0 else 0
            perc_conf = round((stats['confirmados_sin_coincidencia'] / idx) * 100, 2)
            perc_fn = round((stats['falsos_negativos_rescatados'] / idx) * 100, 2)
            logger.info(
                f"Progress: [{idx}/{len(muestra)}] ({rpm} req/m) | "
                f"Confirmados Sin Coincidencia: {stats['confirmados_sin_coincidencia']} ({perc_conf}%) | "
                f"Falsos Negativos: {stats['falsos_negativos_rescatados']} ({perc_fn}%) | "
                f"Errores HTTP: {stats['errores_http']}"
            )

        if args.delay > 0:
            time.sleep(args.delay)

    # Cierre de sesión IRIS
    try:
        scraper.cerrar()
    except Exception:
        pass

    total = stats["total"]
    duracion = round(time.time() - stats["t0"], 1)
    fidelidad = round((stats["confirmados_sin_coincidencia"] / total) * 100, 2) if total > 0 else 0.0
    tasa_fn = round((stats["falsos_negativos_rescatados"] / total) * 100, 2) if total > 0 else 0.0

    print("\n" + "=" * 72)
    print("📊 RESULTADOS FINALES DE AUDITORÍA DE FIDELIDAD DE IRIS")
    print("=" * 72)
    print(f"  • Muestra Total Auditada:               {total:,} registros")
    print(f"  • Tiempo Total de Auditoría:             {duracion} segundos")
    print(f"  • ✅ Verdaderos Negativos (Confirmados): {stats['confirmados_sin_coincidencia']:,} ({fidelidad}%)")
    print(f"  • 🎯 Falsos Negativos Rescatados:        {stats['falsos_negativos_rescatados']:,} ({tasa_fn}%)")
    print(f"  • ❌ Errores HTTP / Timeouts Intermitentes: {stats['errores_http']:,}")
    print("-" * 72)
    print(f"  📌 NIVEL DE FIDELIDAD CONFIRMADO:        {fidelidad}%")
    print("=" * 72)


if __name__ == "__main__":
    main()
