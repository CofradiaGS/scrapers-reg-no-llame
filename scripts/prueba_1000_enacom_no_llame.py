# -*- coding: utf-8 -*-
"""
Script de Auditoría Masiva: 1.000 Líneas de Registro No Llame en ENACOM Web
Prueba de validación en tiempo real para líneas marcadas como 'no_coincidencia'
en el VPS central, determinando el operador activo real y la tasa de portabilidad.

Uso:
    python scripts/prueba_1000_enacom_no_llame.py --limit 1000 --workers 6
    python scripts/prueba_1000_enacom_no_llame.py --limit 50 --workers 3
"""
import sys
import os
import time
import json
import sqlite3
import argparse
import threading
from queue import Queue, Empty
from datetime import datetime

# Asegurar UTF-8 en stdout/stderr para Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Asegurar path raíz del proyecto
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import mysql.connector
from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter
from adapters.enacom.enacom_adapter import EnacomBlockAdapter


def init_local_db(db_path: str = "data/prueba_1000_enacom.sqlite") -> sqlite3.Connection:
    """Inicializa la base SQLite local para almacenar los resultados con modo WAL."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resultados_enacom (
            ani TEXT PRIMARY KEY,
            estado_previo TEXT,
            fuente_previa TEXT,
            operador_bloque TEXT,
            provincia_bloque TEXT,
            localidad_bloque TEXT,
            prestador_original_web TEXT,
            prestador_actual_web TEXT,
            operador_comercial TEXT,
            es_portado INTEGER,
            intentos INTEGER,
            latencia_seg REAL,
            status_enacom TEXT,
            consultado_en TEXT
        )
    """)
    conn.commit()
    return conn


def fetch_no_coincidencia_lines(limit: int = 1000) -> list:
    """Obtiene N líneas con estado 'no_coincidencia' desde el VPS central."""
    print(f"📡 Conectando al VPS Central MySQL ({config.VPS_DBHOST}:{config.VPS_DBPORT})...")
    conn = mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=config.VPS_DBUSER,
        password=config.VPS_DBPASS,
        database=config.VPS_DBNAME,
        connection_timeout=10
    )
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT id, ani, estado, fuente, datos_json 
        FROM queue_registro_no_llame 
        WHERE estado = 'no_coincidencia' 
        LIMIT %s
    """
    cursor.execute(query, (limit,))
    rows = cursor.fetchall()
    conn.close()
    print(f"✅ Descargadas {len(rows)} líneas con 'no_coincidencia' para auditoría.")
    return rows


def worker_loop(
    worker_id: int,
    task_queue: Queue,
    db_conn: sqlite3.Connection,
    db_lock: threading.Lock,
    block_adapter: EnacomBlockAdapter,
    stats: dict,
    stats_lock: threading.Lock,
    stop_event: threading.Event,
    proxy: str = None
):
    """Bucle de ejecución de cada worker con su propio navegador Chromium persistente."""
    adapter = EnacomWebAdapter(headless=True, max_retries=3, proxy=proxy)
    try:
        adapter.iniciar()
        while not stop_event.is_set():
            try:
                item = task_queue.get(timeout=1.0)
            except Empty:
                break

            ani = str(item["ani"]).strip()
            estado_prev = item.get("estado", "")
            fuente_prev = str(item.get("fuente", ""))

            # 1. Enriquecer previamente con bloque estático oficial
            info_bloque = block_adapter.consultar_bloque_dict(ani) or {}
            op_bloque = info_bloque.get("operador_origen", "")
            prov_bloque = info_bloque.get("provincia", "")
            loc_bloque = info_bloque.get("localidad", "")

            # 2. Consultar ENACOM Web en vivo
            t0 = time.time()
            try:
                res = adapter.consultar_linea(Linea(ani=ani))
            except Exception as e:
                res = None
            dt = round(time.time() - t0, 2)

            # 3. Procesar resultado
            if res and res.status == StatusScraping.COINCIDENCIA:
                d = res.detalles
                orig_web = d.get("prestador_original", "")
                act_web = d.get("prestador_actual", "")
                op_comercial = d.get("operador_comercial_actual", "")
                es_port = 1 if d.get("es_portado", False) else 0
                intentos = d.get("intentos_resolucion", 1)
                st_enacom = "coincidencia"
            else:
                orig_web = ""
                act_web = ""
                op_comercial = "Desconocido / Sin Datos"
                es_port = 0
                intentos = 3
                st_enacom = "sin_coincidencia"

            ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 4. Guardar en SQLite local inmediatamente
            with db_lock:
                db_conn.execute("""
                    INSERT OR REPLACE INTO resultados_enacom (
                        ani, estado_previo, fuente_previa, operador_bloque,
                        provincia_bloque, localidad_bloque, prestador_original_web,
                        prestador_actual_web, operador_comercial, es_portado,
                        intentos, latencia_seg, status_enacom, consultado_en
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ani, estado_prev, fuente_prev, op_bloque, prov_bloque,
                    loc_bloque, orig_web, act_web, op_comercial, es_port,
                    intentos, dt, st_enacom, ahora_str
                ))
                db_conn.commit()

            # 5. Actualizar estadísticas
            with stats_lock:
                stats["procesados"] += 1
                if st_enacom == "coincidencia":
                    stats["coincidencias"] += 1
                    stats["operadores"][op_comercial] = stats["operadores"].get(op_comercial, 0) + 1
                    if es_port == 1:
                        stats["portados"] += 1
                else:
                    stats["sin_coincidencia"] += 1

                proc = stats["procesados"]
                tot = stats["total"]
                pct = (proc / tot) * 100
                port_cnt = stats["portados"]
                claro_cnt = stats["operadores"].get("Claro", 0)
                pers_cnt = stats["operadores"].get("Personal", 0)
                mov_cnt = stats["operadores"].get("Movistar", 0)

                tag_p = "🚨 PORTADO" if es_port else "🟢 OK"
                print(
                    f"[{proc}/{tot}] ({pct:.1f}%) ANI: {ani} | {op_comercial} ({tag_p}) | "
                    f"⏱️ {dt}s | Claro:{claro_cnt} Pers:{pers_cnt} Mov:{mov_cnt} Portados:{port_cnt}"
                )

            task_queue.task_done()

    finally:
        adapter.cerrar()


def main():
    parser = argparse.ArgumentParser(description="Auditoría Masiva de ENACOM Web para Líneas 'no_coincidencia'")
    parser.add_argument("--limit", type=int, default=1000, help="Cantidad de líneas a procesar (default 1000)")
    parser.add_argument("--workers", "-w", type=int, default=6, help="Cantidad de workers concurrentes (default 6)")
    parser.add_argument("--proxy", help="Proxy opcional para los workers")
    args = parser.parse_args()

    # 1. Cargar adaptador de bloques oficial
    print("📦 Cargando base estática de bloques oficiales ENACOM...")
    block_adapter = EnacomBlockAdapter()

    # 2. Inicializar DB local
    db_conn = init_local_db()
    db_lock = threading.Lock()

    # 3. Descargar registros del VPS
    rows = fetch_no_coincidencia_lines(limit=args.limit)
    if not rows:
        print("❌ No se encontraron registros con 'no_coincidencia'.")
        return

    # Verificar cuáles ya fueron procesadas en SQLite local
    cursor = db_conn.cursor()
    cursor.execute("SELECT ani FROM resultados_enacom")
    ya_procesadas = set(r[0] for r in cursor.fetchall())

    pendientes = [r for r in rows if str(r["ani"]).strip() not in ya_procesadas]
    print(f"ℹ️ Total descargadas: {len(rows)} | Ya procesadas previamente: {len(ya_procesadas)} | Pendientes: {len(pendientes)}")

    if not pendientes:
        print("✅ Todas las líneas solicitadas ya se encuentran procesadas en SQLite local.")
        return

    task_queue = Queue()
    for item in pendientes:
        task_queue.put(item)

    stats = {
        "total": len(pendientes),
        "procesados": 0,
        "coincidencias": 0,
        "sin_coincidencia": 0,
        "portados": 0,
        "operadores": {}
    }
    stats_lock = threading.Lock()
    stop_event = threading.Event()

    print("=" * 80)
    print(f"🚀 INICIANDO AUDITORÍA ENACOM CON {args.workers} WORKERS PERSISTENTES")
    print(f"🎯 Total líneas a verificar: {len(pendientes)}")
    print("ℹ️ Puedes presionar Ctrl + C en cualquier momento: los datos se guardan al instante fila por fila.")
    print("=" * 80)

    threads = []
    t_inicio = time.time()

    for w_id in range(1, args.workers + 1):
        t = threading.Thread(
            target=worker_loop,
            args=(w_id, task_queue, db_conn, db_lock, block_adapter, stats, stats_lock, stop_event, args.proxy),
            daemon=True
        )
        t.start()
        threads.append(t)
        time.sleep(0.3)  # Arranque escalonado para no saturar CPU en la apertura de Chromium

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
            if task_queue.empty():
                break
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupción recibida (Ctrl + C). Deteniendo workers de forma limpia...")
        stop_event.set()

    for t in threads:
        t.join(timeout=3.0)

    t_total = round(time.time() - t_inicio, 1)

    # 4. Reporte Ejecutivo Final
    print("\n" + "=" * 80)
    print("📊 REPORTE FINAL DE AUDITORÍA ENACOM WEB")
    print("=" * 80)
    print(f"⏱️ Tiempo total de ejecución : {t_total} segundos")
    print(f"📋 Total líneas procesadas   : {stats['procesados']}")
    print(f"🎯 Coincidencias en ENACOM   : {stats['coincidencias']} ({stats['coincidencias']/max(1, stats['procesados'])*100:.1f}%)")
    print(f"🚨 Líneas Portadas           : {stats['portados']} ({stats['portados']/max(1, stats['coincidencias'])*100:.1f}%)")
    print(f"⚠️ Sin Coincidencia en ENACOM: {stats['sin_coincidencia']}")
    print("\n🏢 Distribución por Operador Actual:")
    for op, cnt in sorted(stats["operadores"].items(), key=lambda x: x[1], reverse=True):
        pct = (cnt / max(1, stats["coincidencias"])) * 100
        print(f"   - {op:<25}: {cnt:>5} ({pct:.1f}%)")

    print("\n💾 Resultados consolidados en: data/prueba_1000_enacom.sqlite (tabla 'resultados_enacom')")
    print("=" * 80)


if __name__ == "__main__":
    main()
