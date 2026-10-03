import os
import sys
import time
import json
import sqlite3
import mysql.connector
from dotenv import load_dotenv

# Configuración de codificación UTF-8
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv(r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame\.env")

print("=" * 60)
print("INICIANDO RECUPERACIÓN DE ERRORES DE COBRO EXPRESS EN VPS")
print("=" * 60, flush=True)

# 1. Obtener IDs con error desde staging local
db_staging = r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame\data\staging_local.db"
conn_sqlite = sqlite3.connect(db_staging)
cur_sqlite = conn_sqlite.cursor()

print("1. Extrayendo IDs con status: error desde staging local...", flush=True)
cur_sqlite.execute("""
    SELECT id_vps 
    FROM tareas_staging 
    WHERE tipo_cola = 'cola_automatizacion'
      AND auto_id = 'scraper_cobro_express'
      AND (resultado_json LIKE '%"status": "error"%' OR resultado_json LIKE '%"status":"error"%')
""")
ids_error_staging = [r[0] for r in cur_sqlite.fetchall()]
print(f"   -> Encontrados {len(ids_error_staging):,} IDs con error en staging local.", flush=True)

# 2. Conectar a MySQL VPS
print("\n2. Conectando a MySQL VPS (cola_automatizacion)...", flush=True)
conn_vps = mysql.connector.connect(
    host=os.getenv("VPS_DBHOST", "172.16.20.15"),
    user=os.getenv("COLA_AUTO_DBUSER", "automatizaciones"),
    password=os.getenv("COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
    database=os.getenv("VPS_DBNAME", "bases"),
    port=int(os.getenv("VPS_DBPORT", 3306)),
    autocommit=False
)
cur_vps = conn_vps.cursor()

# 3. Resetear las tareas con estado = 'fallido' en Cobro Express (2.180 aprox)
print("\n3. Reseteando tareas con estado = 'fallido' en cola_automatizacion...", flush=True)
cur_vps.execute("""
    UPDATE cola_automatizacion
    SET estado = 'pendiente',
        resultado = NULL,
        error_msg = NULL,
        fecha_inicio = NULL,
        fecha_fin = NULL
    WHERE auto_id = 'scraper_cobro_express'
      AND estado = 'fallido';
""")
fallidos_reseteados = cur_vps.rowcount
conn_vps.commit()
print(f"   -> Reseteadas {fallidos_reseteados:,} tareas que estaban en 'fallido' a 'pendiente'.", flush=True)

# 4. Resetear en lotes las 41.887 tareas que estaban en 'completado' pero con error WAF
print(f"\n4. Reseteando en VPS los {len(ids_error_staging):,} registros con error WAF (chunks de 2.000)...", flush=True)
chunk_size = 2000
total_waf_reseteados = 0

query_reset = """
    UPDATE cola_automatizacion
    SET estado = 'pendiente',
        resultado = NULL,
        error_msg = NULL,
        fecha_inicio = NULL,
        fecha_fin = NULL
    WHERE id = %s AND auto_id = 'scraper_cobro_express';
"""

# Usar executemany por chunk
for i in range(0, len(ids_error_staging), chunk_size):
    chunk = [(id_val,) for id_val in ids_error_staging[i:i + chunk_size]]
    cur_vps.executemany(query_reset, chunk)
    conn_vps.commit()
    total_waf_reseteados += len(chunk)
    pct = (total_waf_reseteados / len(ids_error_staging)) * 100
    print(f"   [Chunk {i // chunk_size + 1}] Reseteados {total_waf_reseteados:,} / {len(ids_error_staging):,} ({pct:.1f}%)...", flush=True)

conn_vps.close()

# 5. Limpiar en SQLite staging local los registros que se resetearon para permitir re-descarga limpia
print("\n5. Limpiando registros con error en staging_local.db para permitir descarga fresca...", flush=True)
for i in range(0, len(ids_error_staging), 5000):
    chunk_del = ids_error_staging[i:i + 5000]
    placeholders = ",".join("?" * len(chunk_del))
    cur_sqlite.execute(f"""
        DELETE FROM tareas_staging 
        WHERE tipo_cola = 'cola_automatizacion'
          AND id_vps IN ({placeholders});
    """, chunk_del)
conn_sqlite.commit()
conn_sqlite.close()
print(f"   -> Eliminados {len(ids_error_staging):,} registros viejos con error de staging_local.db.", flush=True)

print("\n" + "=" * 60)
print(f"RECUPERACIÓN EXITOSA:")
print(f" - Tareas 'fallido' reseteadas en VPS: {fallidos_reseteados:,}")
print(f" - Tareas con error WAF reseteadas en VPS: {total_waf_reseteados:,}")
print(f" - TOTAL RECUPERADAS Y DEVUELTAS A PENDIENTE: {fallidos_reseteados + total_waf_reseteados:,}")
print("=" * 60, flush=True)
