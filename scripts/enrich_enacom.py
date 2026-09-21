# -*- coding: utf-8 -*-
"""
Script CLI: Enriquecedor Masivo y Consultor de Bloques Oficiales ENACOM
Permite:
1. 'compile': Generar la base binaria ultrarrápida (.dat) desde el Excel oficial.
2. 'lookup <ani>': Consultar un número telefónico individual y ver su ficha técnica completa.
3. 'enrich-db': Enriquecer masivamente la base de datos de producción (queue_registro_no_llame)
   por lotes de alto rendimiento (25.000 a 50.000 registros por commit) con métricas en tiempo real.
"""
import os
import sys
import time
import json
import signal
import argparse
import logging
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from adapters.enacom.enacom_adapter import EnacomBlockAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("EnrichEnacom")

stop_requested = False

def sigint_handler(signum, frame):
    global stop_requested
    print("\n🛑 Señal de interrupción recibida. Finalizando lote actual de forma segura...")
    stop_requested = True

signal.signal(signal.SIGINT, sigint_handler)

def cmd_compile(args):
    excel_path = args.excel or os.path.abspath("data/enacom/enacom_asignaciones.xls")
    dat_path = args.output or os.path.abspath("data/enacom/enacom_lookup.dat")
    
    if not os.path.exists(excel_path):
        print(f"❌ Error: Archivo Excel no encontrado en: {excel_path}")
        sys.exit(1)

    print(f"📦 Compilando catálogo ENACOM desde: {excel_path}")
    print(f"💾 Destino binario (.dat): {dat_path}")
    
    adapter = EnacomBlockAdapter.__new__(EnacomBlockAdapter)
    t0 = time.time()
    adapter._compile_from_excel(excel_path, dat_path)
    print(f"✅ Compilación exitosa en {round(time.time() - t0, 2)}s. Total bloques: {adapter.obtener_total_bloques():,}")

def cmd_lookup(args):
    adapter = EnacomBlockAdapter(args.dat)
    if not adapter.esta_listo():
        print("❌ Error: No se pudo inicializar EnacomBlockAdapter.")
        sys.exit(1)

    ani = args.ani
    res = adapter.consultar_bloque_dict(ani)
    if not res:
        print(f"⚠️ El número {ani} no coincide con ningún bloque oficial del Plan Fundamental de Numeración.")
        sys.exit(0)

    print("\n" + "=" * 70)
    print(f"📱 FICHA TÉCNICA ENACOM PARA ANI: {ani}")
    print("=" * 70)
    print(f"  • Operador de Origen:   {res['operador_origen']}")
    print(f"  • Razón Social Legal:   {res['operador_oficial']}")
    print(f"  • Grupo Económico:      {res['grupo_economico']}")
    print(f"  • Tipo de Línea:        {res['tipo_linea']} (Celular: {res['es_celular']} | WhatsApp: {res['soporta_whatsapp']})")
    print(f"  • Modalidad:            {res['modalidad']} - {res['modalidad_descripcion']}")
    print(f"  • Código de Área:       {res['codigo_area']} ({res['provincia_origen']} - {res['localidad_origen']})")
    print(f"  • Bloque Asignado:      {res['bloque']} (Capacidad: {res['capacidad_bloque']:,} líneas)")
    print(f"  • Rango Oficial:        {res['rango_asignado']}")
    print(f"  • Resolución:           {res['resolucion']} ({res['organismo_emisor']})")
    print(f"  • Fecha Asignación:     {res['fecha_asignacion']} (Año: {res['ano_asignacion']})")
    print("  • Formatos Normalizados:")
    for k, v in res['formatos'].items():
        print(f"      - {k}: {v}")
    print("=" * 70)
    print("\nPayload JSON completo:")
    print(json.dumps(res, indent=2, ensure_ascii=False))

def cmd_enrich_db(args):
    global stop_requested
    batch_size = args.batch_size
    limit = args.limit
    custom_where = args.where

    print("\n" + "=" * 80)
    print("🚀 INICIANDO ENRIQUECIMIENTO MASIVO DE BASE DE DATOS (ENACOM)")
    print("=" * 80)

    # 1. Cargar Adaptador ENACOM en memoria
    adapter = EnacomBlockAdapter(args.dat)
    if not adapter.esta_listo():
        print("❌ Error: No se pudo inicializar EnacomBlockAdapter.")
        sys.exit(1)
    print(f"✅ Motor ENACOM cargado en memoria ({adapter.obtener_total_bloques():,} bloques oficiales).")

    # 2. Conectar a MySQL VPS
    from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
    try:
        queue_adapter = MySQLQueueAdapter(pool_size=2, pool_name="enrich_enacom_pool")
        conn = queue_adapter._get_connection()
    except Exception as e:
        print(f"❌ Error conectando a MySQL VPS (verificar VPN 172.16.20.15): {e}")
        sys.exit(1)

    cur = conn.cursor()

    # 3. Contar registros pendientes de enriquecimiento ENACOM
    where_clauses = ["(datos_json NOT LIKE '%\"enacom\"%' OR datos_json IS NULL)"]
    if custom_where:
        where_clauses.append(f"({custom_where})")
    full_where = " AND ".join(where_clauses)

    print("🔍 Calculando volumen de registros pendientes en la cola...")
    t_cnt = time.time()
    count_query = f"SELECT COUNT(*) FROM queue_registro_no_llame WHERE {full_where}"
    cur.execute(count_query)
    total_pendientes = cur.fetchone()[0]
    print(f"⏱️ Conteo completado en {round(time.time() - t_cnt, 2)}s.")

    total_a_procesar = min(total_pendientes, limit) if limit else total_pendientes

    print(f"📊 Registros en BD sin enriquecimiento ENACOM: {total_pendientes:,}")
    print(f"🎯 Objetivo de procesamiento para esta sesión:   {total_a_procesar:,}")
    print(f"📦 Tamaño de lote por transacción:              {batch_size:,}")
    print("=" * 80 + "\n")

    if total_a_procesar == 0:
        print("🎉 ¡La base de datos ya se encuentra 100% enriquecida con datos de ENACOM!")
        cur.close()
        conn.close()
        return

    # Contadores de telemetría y cursor por Primary Key
    procesados = 0
    last_id = 0
    t_inicio = time.time()
    stats_operadores = {}

    fetch_query = f"""
        SELECT id, ani, datos_json 
        FROM queue_registro_no_llame 
        WHERE id > %s AND {full_where}
        ORDER BY id ASC 
        LIMIT %s
    """
    update_query = """
        UPDATE queue_registro_no_llame 
        SET datos_json = %s, updated_at = CURRENT_TIMESTAMP 
        WHERE id = %s
    """

    while procesados < total_a_procesar and not stop_requested:
        cant_pedir = min(batch_size, total_a_procesar - procesados)
        t_lote_0 = time.time()

        # 1. Fetch de lote indexado por id > last_id
        cur.execute(fetch_query, (last_id, cant_pedir))
        filas = cur.fetchall()
        if not filas:
            break

        last_id = filas[-1][0]

        # 2. Procesamiento ultrarrápido en memoria
        update_data = []
        for row_id, ani_val, raw_json in filas:
            linea_str = str(ani_val).strip()
            datos = {}
            if raw_json:
                if isinstance(raw_json, dict):
                    datos = dict(raw_json)
                elif isinstance(raw_json, str):
                    try:
                        parsed = json.loads(raw_json)
                        if isinstance(parsed, dict):
                            datos = parsed
                        elif isinstance(parsed, list):
                            datos = {"datos_previos_lista": parsed}
                        else:
                            datos = {}
                    except Exception:
                        datos = {}

            if not isinstance(datos, dict):
                datos = {}

            # Resolver bloque ENACOM
            enacom_info = adapter.consultar_bloque_dict(linea_str)
            if enacom_info:
                datos["enacom"] = enacom_info
                op = enacom_info.get("operador_origen", "Otro")
                stats_operadores[op] = stats_operadores.get(op, 0) + 1
            else:
                datos["enacom"] = {
                    "status": "sin_asignacion_oficial",
                    "descripcion": "Prefijo no registrado en Plan Fundamental ENACOM",
                    "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                stats_operadores["Sin Asignar"] = stats_operadores.get("Sin Asignar", 0) + 1

            update_data.append((json.dumps(datos, ensure_ascii=False), row_id))

        # 3. Escritura atómica de ultra alta velocidad usando CASE agrupado (1.000 filas por consulta)
        SUB_CHUNK = 1000
        for i in range(0, len(update_data), SUB_CHUNK):
            chunk = update_data[i:i + SUB_CHUNK]
            cases = []
            params = []
            ids = []
            for json_str, r_id in chunk:
                cases.append(f"WHEN id = {r_id} THEN %s")
                params.append(json_str)
                ids.append(str(r_id))
            
            sql_case = f"UPDATE queue_registro_no_llame SET datos_json = CASE {' '.join(cases)} ELSE datos_json END, updated_at = CURRENT_TIMESTAMP WHERE id IN ({','.join(ids)})"
            cur.execute(sql_case, params)

        conn.commit()

        dur_lote = round(time.time() - t_lote_0, 2)
        procesados += len(filas)
        dur_total = round(time.time() - t_inicio, 1)
        speed = int(procesados / dur_total) if dur_total > 0 else 0

        # Cálculo de ETA
        restantes = total_a_procesar - procesados
        eta_sec = int(restantes / speed) if speed > 0 else 0
        eta_min = round(eta_sec / 60, 1)
        pct = round((procesados / total_a_procesar) * 100, 1)

        # Resumen de operadores del lote actual
        dist_str = ", ".join([f"{k}: {v:,}" for k, v in sorted(stats_operadores.items(), key=lambda x: x[1], reverse=True)[:4]])

        print(
            f"⚡ [{pct:5.1f}%] {procesados:,}/{total_a_procesar:,} | "
            f"Lote: {len(filas):,} en {dur_lote}s ({int(len(filas)/dur_lote) if dur_lote > 0 else 0:,} reg/s) | "
            f"Velocidad: {speed:,} reg/s | ETA: {eta_min}m | [{dist_str}]",
            flush=True
        )

    cur.close()
    conn.close()

    dur_final = round(time.time() - t_inicio, 2)
    print("\n" + "=" * 80)
    print("🏁 RESUMEN FINAL DEL ENRIQUECIMIENTO:")
    print(f"  • Registros procesados y persistidos: {procesados:,}")
    print(f"  • Tiempo total transcurrido:         {dur_final} segundos ({round(dur_final/60, 2)} minutos)")
    print(f"  • Velocidad promedio final:          {int(procesados / dur_final) if dur_final > 0 else 0:,} reg/seg")
    print("  • Distribución por Operador de Origen:")
    for op, cnt in sorted(stats_operadores.items(), key=lambda x: x[1], reverse=True):
        p_op = round((cnt / procesados) * 100, 2) if procesados > 0 else 0
        print(f"      - {op:15}: {cnt:8,} ({p_op:5.2f}%)")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Herramienta CLI de Bloques y Numeración ENACOM")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcomando: compile
    p_comp = subparsers.add_parser("compile", help="Compilar dataset Excel oficial a binario .dat")
    p_comp.add_argument("--excel", help="Ruta al archivo Excel de ENACOM")
    p_comp.add_argument("--output", help="Ruta de destino del archivo binario .dat")

    # Subcomando: lookup
    p_look = subparsers.add_parser("lookup", help="Consultar un ANI individual")
    p_look.add_argument("ani", help="Número telefónico a consultar (ej: 1145678901, 3876858008)")
    p_look.add_argument("--dat", help="Ruta opcional al archivo .dat")

    # Subcomando: enrich-db
    p_enr = subparsers.add_parser("enrich-db", help="Enriquecer masivamente la base de datos MySQL")
    p_enr.add_argument("--batch-size", type=int, default=10000, help="Tamaño del lote por commit (default: 10.000)")
    p_enr.add_argument("--limit", type=int, default=None, help="Límite máximo de registros a procesar")
    p_enr.add_argument("--all", action="store_true", help="Procesar todos los registros pendientes")
    p_enr.add_argument("--where", type=str, default=None, help="Cláusula SQL adicional (ej: estado = 'pendiente')")
    p_enr.add_argument("--dat", help="Ruta opcional al archivo .dat")

    args = parser.parse_args()

    if args.command == "compile":
        cmd_compile(args)
    elif args.command == "lookup":
        cmd_lookup(args)
    elif args.command == "enrich-db":
        cmd_enrich_db(args)

if __name__ == "__main__":
    main()
