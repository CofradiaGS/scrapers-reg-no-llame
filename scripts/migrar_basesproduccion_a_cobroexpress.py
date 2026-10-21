# -*- coding: utf-8 -*-
"""
Script de Migración Industrial en Chunks:
1. Migra cualquier registro exclusivo de `ce_bases_produccion` hacia `scraper_cobro_express`
   con el valor de fuente por defecto ('GSS_PROD').
2. Elimina los registros restantes de `ce_bases_produccion` (duplicados ya existentes en `scraper_cobro_express`)
   en lotes directos de 10.000 (sin JOIN) para garantizar que los workers concurrentes del supervisor activo
   no sufran contención ni bloqueos.
3. Valida que el conteo final de `ce_bases_produccion` sea 0.
"""
import os
import sys
import time

sys.path.insert(0, r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame")

import config
import mysql.connector

def ejecutar_migracion(chunk_delete_size: int = 10000):
    print("Iniciando conexion con MySQL VPS...", flush=True)
    conn = mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=getattr(config, "COLA_AUTO_DBUSER", "automatizaciones"),
        password=getattr(config, "COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
        database=config.VPS_DBNAME,
        use_pure=True,
        autocommit=False
    )
    cursor = conn.cursor(dictionary=True)

    try:
        # 1. Medir estado actual
        cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'ce_bases_produccion'")
        total_actual = cursor.fetchone()["cnt"]
        print(f"Registros actuales en ce_bases_produccion: {total_actual:,}", flush=True)

        if total_actual == 0:
            print("[INFO] ce_bases_produccion ya tiene 0 registros. Nada que procesar.", flush=True)
            return

        # 2. Paso 1: Verificar y migrar exclusivos si quedara alguno
        print("\nPaso 1: Verificando si existen registros exclusivos pendientes de migrar...", flush=True)
        cursor.execute("""
            SELECT b.id
            FROM cola_automatizacion b
            LEFT JOIN cola_automatizacion s
                ON s.auto_id = 'scraper_cobro_express'
               AND s.numero_de_linea = b.numero_de_linea
            WHERE b.auto_id = 'ce_bases_produccion'
              AND s.id IS NULL
        """)
        exclusive_ids = [r['id'] for r in cursor.fetchall()]
        print(f"  Registros exclusivos pendientes encontrados: {len(exclusive_ids):,}", flush=True)

        if exclusive_ids:
            chunk_size = 2000
            for i in range(0, len(exclusive_ids), chunk_size):
                chunk = exclusive_ids[i:i + chunk_size]
                placeholders = ",".join(["%s"] * len(chunk))
                cursor.execute(f"""
                    UPDATE cola_automatizacion
                    SET auto_id = 'scraper_cobro_express',
                        fuente = 'GSS_PROD'
                    WHERE id IN ({placeholders})
                """, chunk)
                conn.commit()
                print(f"    Migrados {i + len(chunk)}/{len(exclusive_ids)} exclusivos...", flush=True)

        # 3. Paso 2: Purgar los duplicados restantes en lotes directos de 10.000 sin JOIN
        print(f"\nPaso 2: Purgando registros duplicados en lotes directos de {chunk_delete_size:,}...", flush=True)
        total_purgados = 0
        t_inicio = time.time()

        while True:
            t0 = time.time()
            cursor.execute(f"DELETE FROM cola_automatizacion WHERE auto_id = 'ce_bases_produccion' LIMIT {chunk_delete_size}")
            afectados = cursor.rowcount
            conn.commit()
            t_chunk = time.time() - t0

            if afectados == 0:
                break

            total_purgados += afectados
            print(f"  [PURGA] Eliminados {total_purgados:,} registros duplicados... ({t_chunk:.2f}s el lote)", flush=True)

        t_total = time.time() - t_inicio
        print(f"Paso 2 completado: {total_purgados:,} duplicados purgados en {t_total:.1f}s.", flush=True)

        # 4. Verificación final
        cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'ce_bases_produccion'")
        remanentes = cursor.fetchone()["cnt"]

        print(f"\n--- VERIFICACION FINAL ---", flush=True)
        print(f"Remanentes en ce_bases_produccion: {remanentes} (esperado: 0)", flush=True)

        if remanentes == 0:
            print("[OK] Migracion y purga de ce_bases_produccion completada al 100%.", flush=True)
        else:
            print(f"[WARNING] Advertencia: Aun quedan {remanentes} registros en ce_bases_produccion.", flush=True)

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Excepcion durante la migracion: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    ejecutar_migracion()
