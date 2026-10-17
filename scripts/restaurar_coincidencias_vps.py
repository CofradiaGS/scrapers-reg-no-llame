# -*- coding: utf-8 -*-
"""
Script de Restauración de Emergencia de Alta Velocidad (Streaming Indexado):
Lee de SQLite usando idx_staging_tipo_auto_estado y restaura en MySQL VPS por PRIMARY KEY en lotes de 1.000.
"""

import os
import sys
import json
import time
import sqlite3
import mysql.connector
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

print("=" * 70)
print("RESTAURACIÓN DE EMERGENCIA DE ALTA VELOCIDAD A MYSQL VPS")
print("=" * 70, flush=True)

# 1. Conexión a SQLite
conn_sq = sqlite3.connect("data/staging_local.db")
cur_sq = conn_sq.cursor()

print("1. Buscando tareas con resultado usando índice local...", flush=True)
cur_sq.execute("""
    SELECT id_vps, resultado_json, scrapers_intentados
    FROM tareas_staging
    WHERE tipo_cola = 'cola_automatizacion' 
      AND auto_id = 'scraper_cobro_express'
      AND resultado_json IS NOT NULL AND resultado_json != '';
""")

# 2. Conectar a MySQL VPS
print("2. Conectando a MySQL VPS (bases.cola_automatizacion)...", flush=True)
conn_vps = mysql.connector.connect(
    host=os.getenv("VPS_DBHOST"),
    user=os.getenv("COLA_AUTO_DBUSER"),
    password=os.getenv("COLA_AUTO_DBPASS"),
    database=os.getenv("VPS_DBNAME"),
    port=int(os.getenv("VPS_DBPORT", 3306)),
    autocommit=False
)
cur_vps = conn_vps.cursor()

query_restore = """
    UPDATE cola_automatizacion
    SET estado = 'completado',
        resultado = %s,
        scrapers_intentados = %s,
        fecha_fin = NOW()
    WHERE id = %s;
"""

batch_size = 1000
batch = []
total_evaluados = 0
total_coincidencias = 0
total_restaurados_vps = 0
t_inicio = time.time()

print("3. Procesando y transmitiendo coincidencias en streaming a MySQL VPS...", flush=True)

while True:
    rows = cur_sq.fetchmany(5000)
    if not rows:
        break
    
    for id_vps, res_str, sc_int in rows:
        total_evaluados += 1
        try:
            r = json.loads(res_str)
            tiene_coincidencia = False
            for k in ['claro', 'movistar', 'personal', 'iris']:
                if isinstance(r.get(k), dict) and r[k].get('status') == 'coincidencia':
                    tiene_coincidencia = True
                    break
            
            if tiene_coincidencia:
                total_coincidencias += 1
                batch.append((res_str, sc_int or "telcos", id_vps))
                
                if len(batch) >= batch_size:
                    cur_vps.executemany(query_restore, batch)
                    conn_vps.commit()
                    total_restaurados_vps += len(batch)
                    batch = []
                    
                    if total_restaurados_vps % 10000 == 0:
                        elapsed = time.time() - t_inicio
                        tasa = total_restaurados_vps / elapsed if elapsed > 0 else 0
                        print(f"   -> Restaurados {total_restaurados_vps:,} registros en VPS | Velocidad: {tasa:,.0f} reg/s | Evaluados: {total_evaluados:,}", flush=True)
        except Exception as e:
            pass

# Procesar remanente
if batch:
    cur_vps.executemany(query_restore, batch)
    conn_vps.commit()
    total_restaurados_vps += len(batch)
    batch = []

conn_vps.close()
conn_sq.close()

elapsed = time.time() - t_inicio
print("\n" + "=" * 70)
print("RESTAURACIÓN FINALIZADA CON ÉXITO:")
print(f" - Total tareas locales evaluadas: {total_evaluados:,}")
print(f" - Coincidencias detectadas: {total_coincidencias:,}")
print(f" - Registros actualizados a 'completado' en VPS: {total_restaurados_vps:,}")
print(f" - Tiempo total: {elapsed:.1f} segundos")
if elapsed > 0:
    print(f" - Velocidad promedio: {total_restaurados_vps / elapsed:,.0f} registros/segundo")
print("=" * 70, flush=True)
