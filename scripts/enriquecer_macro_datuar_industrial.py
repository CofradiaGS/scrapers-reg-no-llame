# -*- coding: utf-8 -*-
"""
Enriquecedor Masivo Industrial Banco Macro - Datuar vía HTTP Directo (Tor Multi-Circuito)
(ESTRICTAMENTE SIN NAVEGADOR)
"""
import sys
import json
import time
import random
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import bs4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/122.0.0.0",
]


TOR_PORT = 9058

def fetch_datuar(dni: str):
    dni_limpio = "".join(filter(str.isdigit, str(dni)))
    if not dni_limpio or len(dni_limpio) < 6:
        return dni, None

    for intento in range(2):
        token = random.randint(1000, 9999999)
        proxy = f"socks5h://user_{token}:tor@127.0.0.1:{TOR_PORT}"
        proxies = {"http": proxy, "https": proxy}
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://datuar.com/",
        }
        try:
            r = requests.get(
                f"https://datuar.com/indext.php?busqueda={dni_limpio}",
                headers=headers,
                proxies=proxies,
                timeout=6.0
            )
            if r.status_code == 200:
                soup = bs4.BeautifulSoup(r.text, "html.parser")
                el = soup.find(attrs={"data-nombre-completo": True})
                if el:
                    attrs = el.attrs
                    raw_name = attrs.get("data-nombre-completo", "").strip()
                    cuil = attrs.get("data-cdu", "").strip()
                    edad_str = attrs.get("data-edad", "").strip()
                    gen_raw = attrs.get("data-genero", "").strip().lower()
                    prov = attrs.get("data-provincia", "").strip().title()
                    ciudad = attrs.get("data-ciudad", "").strip().title()
                    muni = attrs.get("data-municipio", "").strip().title()

                    genero = ""
                    if gen_raw == "m":
                        genero = "Masculino"
                    elif gen_raw == "f":
                        genero = "Femenino"

                    edad = int(edad_str) if edad_str.isdigit() else None

                    return dni, {
                        "dni": dni_limpio,
                        "nombre_completo": raw_name,
                        "cuil": cuil,
                        "edad": edad,
                        "genero": genero,
                        "provincia": prov,
                        "ciudad": ciudad,
                        "municipio": muni,
                    }
                if "0 resultados" in r.text:
                    continue
        except Exception:
            continue
    return dni, None


def main(max_records=None, num_workers=6):
    if not DB_PATH.exists():
        print(f"Error: Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    c = conn.cursor()

    query = "SELECT dni FROM personas WHERE edad IS NULL"
    if max_records:
        query += f" LIMIT {max_records}"

    c.execute(query)
    pendientes = [r[0] for r in c.fetchall()]
    total = len(pendientes)
    print(f"🎯 Total pendientes a enriquecer con Datuar: {total}")

    if total == 0:
        print("✅ No hay registros pendientes.")
        conn.close()
        return

    print(f"🚀 Iniciando enriquecimiento con {num_workers} workers HTTP vía Tor...")
    exitos = 0
    sin_datos = 0
    t0 = time.time()
    lote = 0

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(fetch_datuar, dni): dni for dni in pendientes}

        for idx, fut in enumerate(as_completed(futures), 1):
            dni, data = fut.result()

            if data and data.get("edad") is not None:
                exitos += 1
                edad = data["edad"]
                ciudad = data.get("ciudad") or ""
                muni = data.get("municipio") or ""
                prov = data.get("provincia") or ""
                genero = data.get("genero") or ""
                cuil = data.get("cuil") or ""

                c.execute("SELECT datos_json, localidad, provincia, genero, cuit FROM personas WHERE dni = ?", (dni,))
                row = c.fetchone()
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

                datos_json["datuar"] = data
                nueva_loc = loc_ex if loc_ex.strip() else ciudad
                nueva_prov = prov_ex if prov_ex.strip() else prov
                nuevo_gen = gen_ex if gen_ex.strip() else genero
                nuevo_cuit = cuit_ex if cuit_ex.strip() else cuil
                ahora = time.strftime("%Y-%m-%d %H:%M:%S")

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
                    muni,
                    nueva_loc,
                    nueva_prov,
                    nuevo_gen,
                    nuevo_cuit,
                    json.dumps(datos_json, ensure_ascii=False),
                    ahora,
                    dni
                ))
            else:
                sin_datos += 1

            lote += 1
            if lote >= 20:
                conn.commit()
                lote = 0

            if idx % 10 == 0 or idx == total:
                elapsed = time.time() - t0
                tasa = idx / elapsed if elapsed > 0 else 0
                restante = (total - idx) / tasa if tasa > 0 else 0
                print(f"⏳ [{idx}/{total}] ({idx*100/total:.1f}%) | "
                      f"✅ Con Edad: {exitos} | ❌ Sin datos: {sin_datos} | "
                      f"⚡ {tasa:.1f} reg/s | ⏱️ Restante: {restante:.0f}s", flush=True)

    if lote > 0:
        conn.commit()

    conn.close()
    duracion = time.time() - t0
    print(f"\n🎉 FINALIZADO en {duracion:.1f}s.")
    print(f"   Total: {total} | Con éxito: {exitos} ({(exitos*100/total) if total else 0:.1f}%) | Sin datos: {sin_datos}")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
    main(max_records=lim, num_workers=w)
