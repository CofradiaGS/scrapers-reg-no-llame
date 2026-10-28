# -*- coding: utf-8 -*-
"""
Script de Reconciliación Inmediata de Tareas 'en_proceso' (Cobro Express):
1. Consulta los registros de 'scraper_cobro_express' en estado 'en_proceso' en MySQL VPS.
2. Cruza con el staging local SQLite ('data/staging_local.db'):
   - Si tienen resultado en SQLite -> Sincroniza a MySQL VPS como 'completado' con su JSON y scrapers_intentados.
   - Si NO tienen resultado en SQLite -> Pasa directamente a 'pendiente' en MySQL VPS (reseteando instance_id, fecha_inicio, error_msg).
3. Marca en SQLite local como 'sincronizado'.
"""
import os
import sys
import time
import sqlite3
import mysql.connector
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import config

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def main():
    print("=" * 75)
    print("RECONCILIACIÓN INMEDIATA DE TAREAS 'en_proceso' (COBRO EXPRESS)")
    print("=" * 75, flush=True)

    t0 = time.time()
    
    # 1. Conectar a MySQL VPS
    print("1. Conectando a MySQL VPS...", flush=True)
    conn_vps = mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=config.COLA_AUTO_DBUSER,
        password=config.COLA_AUTO_DBPASS,
        database=config.VPS_DBNAME,
        connection_timeout=20,
        autocommit=False,
        use_pure=True
    )
    cur_vps = conn_vps.cursor(dictionary=True)

    # 2. Obtener los IDs en 'en_proceso'
    print("2. Obteniendo registros en 'en_proceso'...", flush=True)
    cur_vps.execute("""
        SELECT id
        FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
        WHERE auto_id = 'scraper_cobro_express'
          AND estado = 'en_proceso'
        ORDER BY id ASC;
    """)
    rows_vps = cur_vps.fetchall()
    total_en_proceso = len(rows_vps)
    print(f"   -> Encontrados {total_en_proceso:,} registros en 'en_proceso'.", flush=True)

    if total_en_proceso == 0:
        print("✅ No hay registros en 'en_proceso'. Todo en orden.")
        conn_vps.close()
        return

    # 3. Cruzar con SQLite local
    db_staging = PROJECT_ROOT / "data" / "staging_local.db"
    conn_sq = sqlite3.connect(str(db_staging), timeout=60.0)
    cur_sq = conn_sq.cursor()

    ids_vps = [r["id"] for r in rows_vps]
    con_resultado = []
    sin_resultado = []

    print("3. Cruzando IDs con SQLite local (staging_local.db)...", flush=True)
    chunk_size = 1000
    for i in range(0, total_en_proceso, chunk_size):
        chunk = ids_vps[i:i + chunk_size]
        pl = ",".join("?" * len(chunk))
        cur_sq.execute(f"""
            SELECT id_vps, resultado_json, scrapers_intentados
            FROM tareas_staging
            WHERE id_vps IN ({pl})
              AND resultado_json IS NOT NULL
              AND resultado_json != ''
        """, chunk)
        res_dict = {row[0]: (row[1], row[2]) for row in cur_sq.fetchall()}
        for v_id in chunk:
            if v_id in res_dict:
                con_resultado.append((v_id, res_dict[v_id][0], res_dict[v_id][1]))
            else:
                sin_resultado.append(v_id)

    print(f"   • Con resultado listo en SQLite: {len(con_resultado):,} ➔ Sincronizar a 'completado'")
    print(f"   • Sin resultado en SQLite:       {len(sin_resultado):,} ➔ Devolver a 'pendiente'", flush=True)

    # 4. Sincronizar a 'completado' los que tienen resultado
    if con_resultado:
        print("\n4. Impactando registros completados en MySQL VPS...", flush=True)
        sub_chunk_size = 500
        for sc_i in range(0, len(con_resultado), sub_chunk_size):
            sub_lote = con_resultado[sc_i:sc_i + sub_chunk_size]
            row_placeholders = ", ".join(["ROW(%s, %s, %s)"] * len(sub_lote))
            sub_params = []
            for id_vps, res_json, sc_int in sub_lote:
                sub_params.extend([id_vps, res_json, sc_int or "telcos"])

            query_batch_vps = f"""
                UPDATE cola_automatizacion c
                JOIN (VALUES {row_placeholders}) AS t(id, resultado, scrapers_intentados)
                ON c.id = t.id
                SET c.estado = 'completado',
                    c.resultado = t.resultado,
                    c.scrapers_intentados = t.scrapers_intentados,
                    c.instance_id = NULL,
                    c.fecha_fin = NOW();
            """
            cur_vps.execute(query_batch_vps, sub_params)
            conn_vps.commit()

        # Marcar como sincronizados en SQLite
        ids_comp = [r[0] for r in con_resultado]
        for sc_i in range(0, len(ids_comp), chunk_size):
            sub_ids = ids_comp[sc_i:sc_i + chunk_size]
            pl_up = ",".join("?" * len(sub_ids))
            cur_sq.execute(f"""
                UPDATE tareas_staging
                SET estado_local = 'sincronizado',
                    fecha_sincronizado = datetime('now')
                WHERE id_vps IN ({pl_up});
            """, sub_ids)
            conn_sq.commit()

        print(f"   ✅ {len(con_resultado):,} registros sincronizados como 'completado'.", flush=True)

    # 5. Pasar a 'pendiente' los que no tienen resultado
    if sin_resultado:
        print("\n5. Pasando a 'pendiente' los registros sin procesar...", flush=True)
        sub_chunk_size = 1000
        for sc_i in range(0, len(sin_resultado), sub_chunk_size):
            sub_ids = sin_resultado[sc_i:sc_i + sub_chunk_size]
            pl_up = ",".join(["%s"] * len(sub_ids))
            cur_vps.execute(f"""
                UPDATE cola_automatizacion
                SET estado = 'pendiente',
                    instance_id = NULL,
                    error_msg = NULL,
                    fecha_inicio = NULL,
                    fecha_fin = NULL
                WHERE id IN ({pl_up});
            """, sub_ids)
            conn_vps.commit()

        print(f"   ✅ {len(sin_resultado):,} registros reseteados a 'pendiente'.", flush=True)

    conn_sq.close()
    conn_vps.close()

    total_time = time.time() - t0
    print("\n" + "=" * 75)
    print(f"RESUMEN FINAL: {total_en_proceso:,} registros reconciliados con éxito en {total_time:.2f}s")
    print(f"  - Completados: {len(con_resultado):,}")
    print(f"  - Pendientes:  {len(sin_resultado):,}")
    print("=" * 75, flush=True)

if __name__ == "__main__":
    main()
