# -*- coding: utf-8 -*-
"""
Script de Reprocesamiento Atómico para 'sin_coincidencia' de Datuar.
Preserva intactas el 100% de las coincidencias positivas confirmadas (1,108+ registros).
Resetea a 'pendiente' para re-evaluación con triple confirmación independiente:
1. Registros en 'listo_para_subir' catalogados como 'sin_coincidencia' en Datuar.
2. Registros en 'en_proceso' interrumpidos durante la parada controlada del supervisor.
"""
import sqlite3
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ReprocesarDatuar")

conn = sqlite3.connect("data/staging_local.db")
c = conn.cursor()

# 1. Analizar estado previo
c.execute("SELECT estado_local, COUNT(*) FROM tareas_staging GROUP BY estado_local")
logger.info(f"Estado antes del reprocesamiento: {c.fetchall()}")

# 2. Identificar y resetear los 'sin_coincidencia' de listo_para_subir
c.execute("""
    SELECT id_vps, tipo_cola, payload_origen, resultado_json, descripcion 
    FROM tareas_staging 
    WHERE estado_local = 'listo_para_subir'
""")
rows_listo = c.fetchall()

coincidencias_ok = 0
sin_coincidencias_reset = 0

for r in rows_listo:
    id_vps, tipo_cola, p_raw, res_raw, desc = r
    res = json.loads(res_raw) if res_raw else {}
    dat_res = res.get("datuar") or {}
    
    # Si tiene titular válido o status coincidencia, NO TOCAR
    if dat_res.get("status") == "coincidencia" or (dat_res.get("titular") and dat_res["titular"].get("nombre")):
        coincidencias_ok += 1
        continue
    
    # Es un sin_coincidencia que debe reprocesarse
    p = json.loads(p_raw) if p_raw else {}
    if "datuar" in p:
        p.pop("datuar", None)
        
    c.execute("""
        UPDATE tareas_staging
        SET estado_local = 'pendiente',
            scraper_actual = 'datuar',
            resultado_json = NULL,
            payload_origen = ?,
            descripcion = NULL,
            latencia = 0.0,
            error_msg = NULL,
            fecha_lease = NULL,
            fecha_procesado = NULL
        WHERE id_vps = ? AND tipo_cola = ?
    """, (json.dumps(p) if p else p_raw, id_vps, tipo_cola))
    sin_coincidencias_reset += 1

# 3. Resetear registros que quedaron en 'en_proceso' a 'pendiente'
c.execute("""
    UPDATE tareas_staging
    SET estado_local = 'pendiente',
        fecha_lease = NULL
    WHERE estado_local = 'en_proceso'
""")
en_proceso_reset = c.rowcount

conn.commit()

# 4. Estado posterior
c.execute("SELECT estado_local, COUNT(*) FROM tareas_staging GROUP BY estado_local")
logger.info(f"Estado después del reprocesamiento: {c.fetchall()}")

print("\n" + "="*65)
print(f"[OK] COINCIDENCIAS POSITIVAS PRESERVADAS INTACTAS: {coincidencias_ok}")
print(f"[RESET] 'SIN COINCIDENCIA' RESETEADOS A PENDIENTE (Datuar): {sin_coincidencias_reset}")
print(f"[RESET] 'EN PROCESO' LIBERADOS A PENDIENTE:                 {en_proceso_reset}")
print("="*65 + "\n")

conn.close()
