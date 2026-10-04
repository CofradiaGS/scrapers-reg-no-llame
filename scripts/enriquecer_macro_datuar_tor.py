# -*- coding: utf-8 -*-
"""
Enriquecedor Masivo Banco Macro - Datuar vía HTTP con Tor Stream Isolation
(ESTRICTAMENTE SIN NAVEGADOR)
"""
import sys
import os
import json
import time
import sqlite3
import random
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter
from core.domain.entities import Linea, StatusScraping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"


def enriquecer_datuar(max_records=None, num_workers=5):
    if not DB_PATH.exists():
        print(f"Error: Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    c = conn.cursor()

    query = "SELECT dni, nombre_oficial, cuit FROM personas WHERE edad IS NULL"
    if max_records:
        query += f" LIMIT {max_records}"

    c.execute(query)
    pendientes = c.fetchall()
    total = len(pendientes)
    print(f"🎯 Registros pendientes de Datuar: {total}")

    if total == 0:
        print("✅ Todos los registros ya cuentan con edad y demografía.")
        conn.close()
        return

    thread_local = threading.local()

    def get_adapter():
        if not hasattr(thread_local, "adapter"):
            slot = threading.get_ident() % 100
            thread_local.adapter = DatuarAdapter(
                use_tor=True,
                tor_external_daemon=True,
                worker_slot=slot,
                delay_min=0.1,
                delay_max=0.3
            )
            thread_local.adapter.iniciar()
        return thread_local.adapter

    db_lock = threading.Lock()
    actualizados = 0
    sin_datos = 0
    errores = 0
    t0 = time.time()

    def procesar_registro(item):
        nonlocal actualizados, sin_datos, errores
        dni, nombre_oficial, cuit = item
        adapter = get_adapter()
        try:
            linea = Linea(ani=str(dni), dni=str(dni))
            res = adapter.consultar_linea(linea)

            if res.status == StatusScraping.COINCIDENCIA and res.titular and res.titular.edad:
                tit = res.titular
                edad_val = int(tit.edad) if str(tit.edad).isdigit() else None
                ciudad = tit.ciudad or ""
                municipio = tit.municipio or ""
                provincia = tit.provincia or ""
                genero = "Masculino" if tit.genero == "m" else ("Femenino" if tit.genero == "f" else "")
                cuil = tit.cuil or ""
                detalles = res.detalles or {}

                with db_lock:
                    ahora = time.strftime("%Y-%m-%d %H:%M:%S")
                    c_inner = conn.cursor()
                    c_inner.execute("SELECT datos_json, localidad, provincia, genero, cuit FROM personas WHERE dni = ?", (dni,))
                    row = c_inner.fetchone()
                    datos_json = {}
                    loc_ex = ""
                    prov_ex = ""
                    gen_ex = ""
                    cuit_ex = ""
                    if row:
                        if row[0]:
                            try:
                                datos_json = json.loads(row[0])
                            except Exception:
                                datos_json = {}
                        loc_ex = row[1] or ""
                        prov_ex = row[2] or ""
                        gen_ex = row[3] or ""
                        cuit_ex = row[4] or ""

                    datos_json["datuar"] = detalles
                    nueva_loc = loc_ex if loc_ex.strip() else ciudad
                    nueva_prov = prov_ex if prov_ex.strip() else provincia
                    nuevo_gen = gen_ex if gen_ex.strip() else genero
                    nuevo_cuit = cuit_ex if cuit_ex.strip() else cuil

                    c_inner.execute("""
                        UPDATE personas
                        SET edad = ?,
                            ciudad = COALESCE(NULLIF(?, ''), ciudad),
                            municipio = COALESCE(NULLIF(?, ''), municipio),
                            localidad = COALESCE(NULLIF(localidad, ''), ?),
                            provincia = COALESCE(NULLIF(provincia, ''), ?),
                            genero = COALESCE(NULLIF(genero, ''), ?),
                            cuit = COALESCE(NULLIF(cuit, ''), ?),
                            datos_json = ?,
                            fecha_actualizacion = ?
                        WHERE dni = ?
                    """, (
                        edad_val,
                        ciudad,
                        municipio,
                        nueva_loc,
                        nueva_prov,
                        nuevo_gen,
                        nuevo_cuit,
                        json.dumps(datos_json, ensure_ascii=False),
                        ahora,
                        dni
                    ))
                    conn.commit()
                    actualizados += 1
                return True
            else:
                with db_lock:
                    sin_datos += 1
                return False
        except Exception as e:
            with db_lock:
                errores += 1
            return False

    print(f"🚀 Iniciando enriquecimiento HTTP ({num_workers} workers vía Tor, SIN NAVEGADOR)...")
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(procesar_registro, item): item for item in pendientes}

        for idx, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if idx % 20 == 0 or idx == total:
                elapsed = time.time() - t0
                tasa = idx / elapsed if elapsed > 0 else 0
                restante = (total - idx) / tasa if tasa > 0 else 0
                print(f"⏳ [{idx}/{total}] ({idx*100/total:.1f}%) | "
                      f"✅ Enriquecidos: {actualizados} | ❌ Sin datos: {sin_datos} | "
                      f"⚡ {tasa:.1f} reg/s | ⏱️ Restante: {restante:.0f}s")

    conn.close()
    duracion = time.time() - t0
    print(f"\n✨ FIN ENRIQUECIMIENTO DATUAR en {duracion:.1f}s.")
    print(f"   Total: {total} | Con éxito: {actualizados} | Sin datos: {sin_datos} | Errores: {errores}")


if __name__ == "__main__":
    max_rec = int(sys.argv[1]) if len(sys.argv) > 1 else None
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    enriquecer_datuar(max_records=max_rec, num_workers=workers)
