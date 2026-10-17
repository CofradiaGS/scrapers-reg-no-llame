# -*- coding: utf-8 -*-
"""
Sincronización Total de Cobro Express (Ultra Alta Velocidad):
1. Consulta en MySQL VPS los IDs que están en 'en_proceso' y 'pendiente'.
2. Busca sus resultados en SQLite local (en bloques de 1.000).
3. Impacta de inmediato en MySQL VPS como 'completado' con resultado y scrapers_intentados.
4. Marca en SQLite local como 'sincronizado'.
"""

import os
import sys
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

print("=" * 75)
print("SINCRONIZACIÓN PRIORITARIA DE COBRO EXPRESS (VPS TARGET DRIVEN)")
print("=" * 75, flush=True)

# 1. Conectar a MySQL VPS
print("1. Conectando a MySQL VPS...", flush=True)
conn_vps = mysql.connector.connect(
    host=os.getenv("VPS_DBHOST"),
    user=os.getenv("COLA_AUTO_DBUSER"),
    password=os.getenv("COLA_AUTO_DBPASS"),
    database=os.getenv("VPS_DBNAME"),
    port=int(os.getenv("VPS_DBPORT", 3306)),
    autocommit=False
)
cur_vps = conn_vps.cursor(dictionary=True)

# 2. Extraer los IDs candidatos directamente de MySQL (priorizando en_proceso)
print("2. Obteniendo registros en 'en_proceso' y 'pendiente' desde MySQL VPS...", flush=True)
cur_vps.execute("""
    SELECT id, estado
    FROM cola_automatizacion
    WHERE auto_id = 'scraper_cobro_express' 
      AND estado IN ('en_proceso', 'pendiente')
    ORDER BY (estado = 'en_proceso') DESC, id ASC;
""")
vps_rows = cur_vps.fetchall()

total_en_proceso = sum(1 for r in vps_rows if r['estado'] == 'en_proceso')
total_pendiente = sum(1 for r in vps_rows if r['estado'] == 'pendiente')
total_candidatos = len(vps_rows)

print(f"   • Tareas en 'en_proceso' en VPS: {total_en_proceso:,}")
print(f"   • Tareas en 'pendiente' en VPS:  {total_pendiente:,}")
print(f"   • Total tareas a verificar:     {total_candidatos:,}", flush=True)

if total_candidatos == 0:
    print("\n✅ No hay tareas en 'en_proceso' ni 'pendiente' en el VPS. Todo está completado.")
    conn_vps.close()
    sys.exit(0)

# 3. Conectar a SQLite local para cruzar resultados
print("\n3. Cruzando con base de datos local y subiendo a MySQL VPS...", flush=True)
conn_sq = sqlite3.connect("data/staging_local.db", timeout=60.0)
cur_sq = conn_sq.cursor()

query_vps_update = """
    UPDATE cola_automatizacion
    SET estado = 'completado',
        resultado = %s,
        scrapers_intentados = %s,
        fecha_fin = NOW()
    WHERE id = %s;
"""

chunk_size = 1000
total_sincronizados = 0
sincronizados_en_proceso = 0
sincronizados_pendiente = 0
t_inicio = time.time()

for i in range(0, total_candidatos, chunk_size):
    chunk_vps = vps_rows[i:i + chunk_size]
    chunk_ids = [r['id'] for r in chunk_vps]
    id_to_estado = {r['id']: r['estado'] for r in chunk_vps}
    
    # Buscar resultados en SQLite local usando la clave primaria id_vps
    pl = ",".join("?" * len(chunk_ids))
    cur_sq.execute(f"""
        SELECT id_vps, resultado_json, scrapers_intentados
        FROM tareas_staging
        WHERE id_vps IN ({pl})
          AND resultado_json IS NOT NULL AND resultado_json != '';
    """, chunk_ids)
    
    resultados_locales = cur_sq.fetchall()
    if not resultados_locales:
        continue
    
    # Preparar parámetros para MySQL VPS
    params = []
    ids_actualizados = []
    for id_vps, res_json, sc_int in resultados_locales:
        params.append((res_json, sc_int or "telcos", id_vps))
        ids_actualizados.append(id_vps)
        if id_to_estado.get(id_vps) == 'en_proceso':
            sincronizados_en_proceso += 1
        else:
            sincronizados_pendiente += 1
            
    # Impactar en MySQL VPS
    cur_vps.executemany(query_vps_update, params)
    conn_vps.commit()
    total_sincronizados += len(params)
    
    # Actualizar estado a 'sincronizado' en SQLite local
    pl_up = ",".join("?" * len(ids_actualizados))
    cur_sq.execute(f"""
        UPDATE tareas_staging
        SET estado_local = 'sincronizado',
            fecha_sincronizado = datetime('now')
        WHERE id_vps IN ({pl_up});
    """, ids_actualizados)
    conn_sq.commit()
    
    if total_sincronizados % 5000 < chunk_size or i + chunk_size >= total_candidatos:
        elapsed = time.time() - t_inicio
        vel = total_sincronizados / elapsed if elapsed > 0 else 0
        pct = (i + len(chunk_vps)) / total_candidatos * 100
        print(f"   [Progreso {pct:.1f}%] Sincronizados: {total_sincronizados:,} (En proceso: {sincronizados_en_proceso:,} | Pendientes: {sincronizados_pendiente:,}) | Vel: {vel:,.0f} reg/s", flush=True)

conn_sq.close()
conn_vps.close()

elapsed_total = time.time() - t_inicio
print("\n" + "=" * 75)
print("SINCRONIZACIÓN EXITOSA FINALIZADA:")
print(f" - Registros de 'en_proceso' subidos a 'completado': {sincronizados_en_proceso:,}")
print(f" - Registros de 'pendiente' subidos a 'completado':  {sincronizados_pendiente:,}")
print(f" - TOTAL IMPACTADOS EN VPS:                         {total_sincronizados:,}")
print(f" - Tiempo transcurrido:                             {elapsed_total:.1f} segundos")
if elapsed_total > 0:
    print(f" - Velocidad efectiva:                              {total_sincronizados / elapsed_total:,.0f} registros/segundo")
print("=" * 75, flush=True)
