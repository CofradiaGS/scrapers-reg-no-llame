import sqlite3, json

conn = sqlite3.connect('data/staging_local.db')
c = conn.cursor()

c.execute("SELECT id_vps, tipo_cola, payload_origen, resultado_json, estado_local, descripcion FROM tareas_staging")
rows = c.fetchall()

reset_count = 0
coincidencias_preserved = 0

for r in rows:
    id_vps, tipo_cola, p_raw, res_raw, estado, desc = r
    p = json.loads(p_raw) if p_raw else {}
    res = json.loads(res_raw) if res_raw else {}
    
    # Check if this record had a genuine coincidencia
    dat_res = res.get("datuar") or p.get("datuar") or {}
    is_genuine_coincidencia = (dat_res.get("status") == "coincidencia" and bool(dat_res.get("titular")))
    
    if is_genuine_coincidencia:
        coincidencias_preserved += 1
        # Keep as listo_para_subir
        continue
    
    # If it was salteado, fallido, or had a false sin_coincidencia in datuar:
    p_changed = False
    if "datuar" in p:
        p.pop("datuar", None)
        p_changed = True
        
    c.execute("""
        UPDATE tareas_staging
        SET estado_local = 'pendiente',
            scraper_actual = 'datuar',
            resultado_json = NULL,
            payload_origen = ?,
            descripcion = NULL,
            latencia = 0.0,
            error_msg = NULL,
            fecha_lease = NULL
        WHERE id_vps = ? AND tipo_cola = ?
    """, (json.dumps(p) if p else p_raw, id_vps, tipo_cola))
    reset_count += 1

conn.commit()

c.execute("SELECT COUNT(*), estado_local, scraper_actual FROM tareas_staging GROUP BY estado_local, scraper_actual")
print("Staging tras limpieza:", c.fetchall())
print(f"Coincidencias preservadas intactas: {coincidencias_preserved}")
print(f"Registros reseteados a pendiente para Datuar: {reset_count}")

conn.close()
