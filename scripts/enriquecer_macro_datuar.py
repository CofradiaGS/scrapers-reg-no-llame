# -*- coding: utf-8 -*-
"""
Enriquecedor Masivo de Banco Macro con Datuar (HTTP Directo - SIN NAVEGADOR)
Consulta el endpoint oficial de búsqueda HTTP (indext.php) de Datuar.
Extrae y persiste: edad, ciudad, municipio, provincia, género y CUIL/CUIT.
"""
import sys
import re
import json
import time
import sqlite3
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Edge/122.0.0.0",
]


def crear_session():
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=2)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def consultar_datuar_http(session: requests.Session, dni: str) -> dict:
    dni_limpio = "".join(filter(str.isdigit, str(dni)))
    if not dni_limpio or len(dni_limpio) < 6:
        return {}

    url = f"https://datuar.com/indext.php?busqueda={dni_limpio}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://datuar.com/",
        "Connection": "keep-alive"
    }

    try:
        resp = session.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return {}

        html = resp.text
        names = re.findall(r'data-nombre-completo="([^"]+)"', html)
        first_names = re.findall(r'data-nombre="([^"]+)"', html)
        cdus = re.findall(r'data-cdu="([^"]+)"', html)
        edades = re.findall(r'data-edad="([^"]+)"', html)
        generos = re.findall(r'data-genero="([^"]+)"', html)
        provincias = re.findall(r'data-provincia="([^"]+)"', html)
        ciudades = re.findall(r'data-ciudad="([^"]+)"', html)
        municipios = re.findall(r'data-municipio="([^"]+)"', html)

        if not names:
            return {}

        raw_name = names[0].strip()
        cuil = cdus[0].strip() if cdus else ""
        edad = edades[0].strip() if edades else ""
        genero_raw = generos[0].strip().lower() if generos else ""
        provincia = provincias[0].strip().title() if provincias else ""
        ciudad = ciudades[0].strip().title() if ciudades else ""
        municipio = municipios[0].strip().title() if municipios else ""

        genero = ""
        if genero_raw == "m":
            genero = "Masculino"
        elif genero_raw == "f":
            genero = "Femenino"

        return {
            "dni": dni_limpio,
            "nombre_completo": raw_name,
            "cuil": cuil,
            "edad": int(edad) if edad.isdigit() else None,
            "genero": genero,
            "provincia": provincia,
            "ciudad": ciudad,
            "municipio": municipio,
        }
    except Exception:
        return {}


def main():
    if not DB_PATH.exists():
        print(f"Error: Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    c = conn.cursor()

    # Seleccionar registros donde falta la edad
    c.execute("SELECT dni, nombre_oficial, cuit FROM personas WHERE edad IS NULL")
    pendientes = c.fetchall()
    total = len(pendientes)
    print(f"🎯 Total registros pendientes de enriquecimiento con Datuar: {total}")

    if total == 0:
        print("✅ Todos los registros ya cuentan con edad y datos demográficos.")
        conn.close()
        return

    # Usar ThreadPoolExecutor para procesamiento concurrente rápido por HTTP
    num_workers = 8
    print(f"🚀 Iniciando consulta concurrente por HTTP ({num_workers} threads, SIN NAVEGADOR)...")

    actualizados = 0
    sin_coincidencia = 0
    t0 = time.time()

    # Cada worker tendrá su propia sesión
    import threading
    thread_local = threading.local()

    def get_worker_session():
        if not hasattr(thread_local, "session"):
            thread_local.session = crear_session()
        return thread_local.session

    def worker_task(item):
        dni, nombre_oficial, cuit_act = item
        session = get_worker_session()
        time.sleep(random.uniform(0.1, 0.25))  # cortesía mínima
        data = consultar_datuar_http(session, dni)
        return dni, data

    batch_updates = []
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(worker_task, item): item for item in pendientes}

        for idx, fut in enumerate(as_completed(futures), 1):
            dni, data = fut.result()
            if data and data.get("edad") is not None:
                actualizados += 1
                batch_updates.append(data)
            else:
                sin_coincidencia += 1

            if len(batch_updates) >= 50:
                # Guardar lote
                _persistir_lote(conn, batch_updates)
                batch_updates.clear()

            if idx % 100 == 0 or idx == total:
                elapsed = time.time() - t0
                tasa = idx / elapsed if elapsed > 0 else 0
                restante = (total - idx) / tasa if tasa > 0 else 0
                print(f"⏳ Progreso: {idx}/{total} ({idx*100/total:.1f}%) | "
                      f"Encontrados: {actualizados} | Sin datos: {sin_coincidencia} | "
                      f"Velocidad: {tasa:.1f} reg/s | Restante: {restante:.0f}s")

    if batch_updates:
        _persistir_lote(conn, batch_updates)
        batch_updates.clear()

    conn.close()
    tiempo_total = time.time() - t0
    print(f"\n🎉 ENRIQUECIMIENTO FINALIZADO en {tiempo_total:.1f}s.")
    print(f"   Total procesados: {total}")
    print(f"   Enriquecidos con Datuar: {actualizados} ({actualizados*100/total:.1f}%)")
    print(f"   Sin coincidencia: {sin_coincidencia}")


def _persistir_lote(conn, lote):
    c = conn.cursor()
    ahora = time.strftime("%Y-%m-%d %H:%M:%S")
    for d in lote:
        dni = d["dni"]
        edad = d.get("edad")
        ciudad = d.get("ciudad") or ""
        municipio = d.get("municipio") or ""
        provincia = d.get("provincia") or ""
        genero = d.get("genero") or ""
        cuil = d.get("cuil") or ""

        # Leer datos_json existente para enriquecer namespace 'datuar'
        c.execute("SELECT datos_json, localidad, provincia, genero, cuit FROM personas WHERE dni = ?", (dni,))
        row = c.fetchone()
        datos_json = {}
        loc_existente = ""
        prov_existente = ""
        gen_existente = ""
        cuit_existente = ""

        if row:
            if row[0]:
                try:
                    datos_json = json.loads(row[0])
                except Exception:
                    datos_json = {}
            loc_existente = row[1] or ""
            prov_existente = row[2] or ""
            gen_existente = row[3] or ""
            cuit_existente = row[4] or ""

        datos_json["datuar"] = d
        nueva_loc = loc_existente if loc_existente.strip() else ciudad
        nueva_prov = prov_existente if prov_existente.strip() else provincia
        nuevo_gen = gen_existente if gen_existente.strip() else genero
        nuevo_cuit = cuit_existente if cuit_existente.strip() else cuil

        c.execute("""
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
            edad,
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


if __name__ == "__main__":
    main()
