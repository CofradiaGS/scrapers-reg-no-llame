# -*- coding: utf-8 -*-
"""
Script de Migración Atómica en MySQL VPS:
1. Actualiza `fuente = 'datos_mendoza'` en los 20.580 registros de `scraper_cobro_express`
   que coinciden con `ce_datosmendoza`.
2. Elimina los 20.580 registros redundantes de `ce_datosmendoza` para no violar
   la restricción UNIQUE de `idx_upsert_telefono(auto_id, numero_de_linea)`.
3. Actualiza los 2.711 registros restantes de `ce_datosmendoza` asignándoles:
   - `auto_id = 'scraper_cobro_express'`
   - `fuente = 'datos_mendoza'`
4. Valida que el conteo final sea exactamente 23.291 registros de Mendoza en `scraper_cobro_express`.
"""
import os
import sys
import time

sys.path.insert(0, r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame")

import config
import mysql.connector

def ejecutar_migracion():
    print("Iniciando conexión con MySQL VPS...", flush=True)
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
        # 1. Medir estado inicial
        cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'ce_datosmendoza'")
        total_mendoza_inicial = cursor.fetchone()["cnt"]
        print(f"Total registros iniciales en ce_datosmendoza: {total_mendoza_inicial}", flush=True)
        if total_mendoza_inicial == 0:
            print("Ya no hay registros en ce_datosmendoza. Verificando conteo con fuente='datos_mendoza'...", flush=True)
            cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'scraper_cobro_express' AND fuente = 'datos_mendoza'")
            print(f"Registros en scraper_cobro_express con fuente='datos_mendoza': {cursor.fetchone()['cnt']}", flush=True)
            return

        # 2. Paso A: Actualizar fuente='datos_mendoza' en scraper_cobro_express para los que coinciden
        print("Paso 1: Actualizando fuente='datos_mendoza' en scraper_cobro_express para líneas coincidentes...", flush=True)
        query_update_s = """
            UPDATE cola_automatizacion s
            INNER JOIN cola_automatizacion m
                ON s.auto_id = 'scraper_cobro_express'
               AND s.numero_de_linea = m.numero_de_linea
            SET s.fuente = 'datos_mendoza'
            WHERE m.auto_id = 'ce_datosmendoza';
        """
        cursor.execute(query_update_s)
        afectados_s = cursor.rowcount
        print(f"  Filas actualizadas en scraper_cobro_express: {afectados_s}", flush=True)

        # 3. Paso B: Eliminar los duplicados de ce_datosmendoza que ya están en scraper_cobro_express
        print("Paso 2: Eliminando duplicados redundantes de ce_datosmendoza...", flush=True)
        query_delete_m = """
            DELETE m
            FROM cola_automatizacion m
            INNER JOIN cola_automatizacion s
                ON s.auto_id = 'scraper_cobro_express'
               AND s.numero_de_linea = m.numero_de_linea
            WHERE m.auto_id = 'ce_datosmendoza';
        """
        cursor.execute(query_delete_m)
        eliminados_m = cursor.rowcount
        print(f"  Filas eliminadas de ce_datosmendoza: {eliminados_m}", flush=True)

        # 4. Paso C: Mover los restantes de ce_datosmendoza a scraper_cobro_express con fuente='datos_mendoza'
        print("Paso 3: Migrando registros exclusivos de ce_datosmendoza hacia scraper_cobro_express...", flush=True)
        query_migrar_restantes = """
            UPDATE cola_automatizacion
            SET auto_id = 'scraper_cobro_express',
                fuente = 'datos_mendoza'
            WHERE auto_id = 'ce_datosmendoza';
        """
        cursor.execute(query_migrar_restantes)
        migrados_exclusivos = cursor.rowcount
        print(f"  Filas exclusivas migradas a scraper_cobro_express: {migrados_exclusivos}", flush=True)

        # 5. Validación antes del COMMIT
        cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'ce_datosmendoza'")
        remanentes = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) as cnt FROM cola_automatizacion WHERE auto_id = 'scraper_cobro_express' AND fuente = 'datos_mendoza'")
        total_mendoza_final = cursor.fetchone()["cnt"]

        print(f"\n--- VERIFICACIÓN PRE-COMMIT ---", flush=True)
        print(f"Remanentes en ce_datosmendoza: {remanentes} (esperado: 0)", flush=True)
        print(f"Total en scraper_cobro_express con fuente='datos_mendoza': {total_mendoza_final} (esperado: 23291)", flush=True)

        if remanentes == 0 and total_mendoza_final == total_mendoza_inicial:
            conn.commit()
            print("[OK] Transaccion COMMIT realizada exitosamente con 100% de integridad.", flush=True)
        else:
            conn.rollback()
            print("[ERROR] Los conteos no coinciden exactamente. Se ejecuto ROLLBACK preventivo.", flush=True)
            sys.exit(1)

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
