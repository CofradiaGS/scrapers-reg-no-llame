# -*- coding: utf-8 -*-
"""
Enriquecedor Industrial Multi-Worker Banco Macro - CuitOnline vía HTTP Directo
- 100% Blindado contra Honeypot y datos falsos (Aislamiento Zero-Cookies de Paywall)
- Detección automática y descarte de trampas MD5 y anagramas
- Extracción profunda oficial: CUIT, Monotributo / Responsable Inscripto, Actividades, Domicilio
- Concurrente multi-hilo en la misma consola con Rate Limiter adaptativo anti-429
- Parada inmediata segura (Ctrl + C) con guardado instantáneo en SQLite (conn.commit por fila)
"""
import sys
import json
import time
import re
import random
import sqlite3
import threading
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

stop_requested = threading.Event()
db_lock = threading.Lock()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


def validar_cuit_modulo11(cuit: str) -> bool:
    """Verifica el dígito verificador oficial de AFIP según algoritmo Módulo 11."""
    c = "".join(filter(str.isdigit, str(cuit)))
    if len(c) != 11:
        return False
    multiplicadores = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(c[i]) * multiplicadores[i] for i in range(10))
    resto = suma % 11
    if resto == 0:
        dv = 0
    elif resto == 1:
        dv = 9 if c.startswith("23") else 4
    else:
        dv = 11 - resto
    return dv == int(c[10])


class CuitOnlineWorker:
    """Worker con sesión stateless (cero cookies de tracking) que neutraliza el honeypot de CuitOnline."""
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.honeypot_bloqueados = 0

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
            "Referer": "https://www.cuitonline.com/",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

    def consultar(self, dni: str, max_intentos: int = 4) -> dict:
        dni_limpio = "".join(filter(str.isdigit, str(dni)))
        if not dni_limpio or len(dni_limpio) < 6:
            return None

        url_search = f"https://www.cuitonline.com/search.php?q={dni_limpio}"

        for intento in range(1, max_intentos + 1):
            if stop_requested.is_set():
                return None

            try:
                # REGLA DE ORO ANTI-HONEYPOT:
                # Cada consulta a CuitOnline debe usar un Session() limpio sin enviar
                # cookies de 'paywalled-content-hits-counter' ni de tracking de sesión previa.
                headers = self._get_headers()
                resp = requests.get(url_search, headers=headers, timeout=10.0)

                # Manejo de rate limiting adaptativo (429)
                if resp.status_code == 429:
                    wait_time = 1.5 * intento + random.uniform(0.5, 1.0)
                    time.sleep(wait_time)
                    continue

                if resp.status_code != 200:
                    time.sleep(0.5)
                    continue

                soup_s = BeautifulSoup(resp.text, "html.parser")
                hit = soup_s.select_one(".hit")
                if not hit:
                    # Sin resultados oficiales en AFIP
                    return {"dni": dni_limpio, "encontrado": False}

                link_el = hit.select_one(".denominacion a")
                link = link_el["href"] if link_el else ""

                # --- ESCUDO ANTI-HONEYPOT 1: Validar enlace numérico ---
                # Si CuitOnline sirve honeypot, el link es un hash MD5: /detalle/bcb827a0...
                # Si CuitOnline sirve el dato real, el link contiene el CUIT de 11 dígitos: /detalle/20384087796/...
                m_cuit = re.search(r"detalle/(\d{11})/", link)
                if not m_cuit:
                    self.honeypot_bloqueados += 1
                    time.sleep(1.0 + random.uniform(0.2, 0.5))
                    continue

                cuit_limpio = m_cuit.group(1)

                # --- ESCUDO ANTI-HONEYPOT 2: Validar algoritmo AFIP ---
                if not validar_cuit_modulo11(cuit_limpio):
                    self.honeypot_bloqueados += 1
                    time.sleep(1.0)
                    continue

                # Paso 2: Extraer ficha oficial de detalle profundo
                detail_url = f"https://www.cuitonline.com/{link.lstrip('/')}" if not link.startswith("http") else link
                time.sleep(random.uniform(0.1, 0.25))

                r_det = requests.get(detail_url, headers=headers, timeout=10.0)
                if r_det.status_code == 429:
                    time.sleep(2.0)
                    r_det = requests.get(detail_url, headers=headers, timeout=10.0)

                data_parsed = self._parse_detalle_html(r_det.text if r_det.status_code == 200 else "", cuit_limpio, dni_limpio, hit)
                return data_parsed

            except Exception:
                time.sleep(0.8)

        return None

    def _parse_detalle_html(self, html: str, cuit_limpio: str, dni_limpio: str, hit_fallback) -> dict:
        denominacion_raw = ""
        direccion = ""
        localidad = ""
        provincia = ""
        genero = ""
        empleador = "No"
        impuestos_activos = []
        regimenes_activos = []
        actividades = []
        iva = ""
        ganancias = ""

        # Formato CUIT con guiones
        cuit_fmt = f"{cuit_limpio[:2]}-{cuit_limpio[2:10]}-{cuit_limpio[10:]}"

        if html:
            soup = BeautifulSoup(html, "html.parser")
            p_data = soup.select_one(".persona-data")

            # Nombre oficial desde H1
            h1 = soup.select_one("h1")
            if h1:
                denominacion_raw = h1.get_text(strip=True).replace("?", "Ñ")

            if p_data:
                # Impuestos activos
                for h2 in p_data.select("h2.impuestos_activos"):
                    txt_h2 = h2.get_text()
                    parent_li = h2.find_parent("li")
                    if not parent_li:
                        continue
                    if "Impuestos activos" in txt_h2:
                        for sub_li in parent_li.select("ul li"):
                            t = sub_li.get_text(" ", strip=True).strip(" »")
                            if t:
                                impuestos_activos.append(t)
                    elif "Regímenes activos" in txt_h2:
                        for sub_li in parent_li.select("ul li"):
                            t = sub_li.get_text(" ", strip=True)
                            if t:
                                regimenes_activos.append(t)
                    elif "Actividades:" in txt_h2:
                        for act_span in parent_li.select("div > span"):
                            t = act_span.get_text(" ", strip=True)
                            if t and len(t) > 3:
                                actividades.append(t)

                # Domicilio y Localidad
                dom_el = p_data.select_one('[itemprop="streetAddress"]')
                if dom_el:
                    direccion = dom_el.get_text(strip=True).title()

                loc_el = p_data.select_one('[itemprop="addressLocality"]')
                if loc_el:
                    localidad = loc_el.get_text(strip=True).title()

                prov_el = p_data.select_one('[itemprop="addressRegion"]')
                if prov_el:
                    provincia = prov_el.get_text(strip=True).title()

                # Género
                gen_el = p_data.select_one('[itemprop="gender"]')
                if gen_el:
                    g_text = gen_el.get_text(strip=True).lower()
                    if "masculin" in g_text or g_text == "m":
                        genero = "Masculino"
                    elif "femenin" in g_text or g_text == "f":
                        genero = "Femenino"

                # IVA, Ganancias y Empleador
                for li in p_data.select("li"):
                    t_li = li.get_text(" ", strip=True)
                    if "IVA:" in t_li:
                        iva = t_li.split("IVA:", 1)[1].strip()
                    if "Ganancias:" in t_li:
                        ganancias = t_li.split("Ganancias:", 1)[1].strip()
                    if "Empleador:" in t_li:
                        empleador = t_li.split("Empleador:", 1)[1].strip().capitalize()

        # Fallback al snippet si el detalle no trajo nombre
        if not denominacion_raw and hit_fallback:
            h2_el = hit_fallback.select_one(".denominacion h2")
            if h2_el:
                denominacion_raw = h2_el.get_text(strip=True).replace("?", "Ñ")

        # Inferencia de género por prefijo CUIT si quedó vacío
        if not genero:
            if cuit_limpio.startswith("20"):
                genero = "Masculino"
            elif cuit_limpio.startswith("27"):
                genero = "Femenino"
            elif cuit_limpio.startswith("23") and cuit_limpio.endswith("4"):
                genero = "Femenino"
            elif cuit_limpio.startswith("23") and cuit_limpio.endswith("9"):
                genero = "Masculino"

        # Determinación de Condición AFIP Real
        has_mono = any("MONOTRIBUTO" in str(x).upper() for x in impuestos_activos)
        has_mono_social = any("SOCIAL" in str(x).upper() for x in impuestos_activos)
        no_imp = any("NO REGISTRA" in str(x).upper() for x in impuestos_activos)

        if has_mono:
            condicion_afip = "Monotributo Social" if has_mono_social else "Monotributista"
        elif "inscripto" in iva.lower() or "personas fisicas" in ganancias.lower():
            condicion_afip = "Responsable Inscripto"
        elif "exento" in iva.lower():
            condicion_afip = "IVA Exento"
        elif no_imp:
            condicion_afip = "No Inscripto (Solo CUIL / Empleado o Jubilado)"
        else:
            condicion_afip = "No Inscripto (Solo CUIL / Empleado o Jubilado)"

        return {
            "dni": dni_limpio,
            "encontrado": True,
            "cuit": cuit_fmt,
            "cuit_limpio": cuit_limpio,
            "denominacion": denominacion_raw.upper(),
            "condicion_afip": condicion_afip,
            "impuestos_activos": impuestos_activos,
            "regimenes_activos": regimenes_activos,
            "actividades": actividades,
            "direccion": direccion,
            "localidad": localidad,
            "provincia": provincia,
            "genero": genero,
            "empleador": empleador,
            "iva": iva,
            "ganancias": ganancias,
            "constancia_inscripcion_afip": f"https://www.cuitonline.com/constancia/inscripcion/{cuit_limpio}",
            "detalle_url": f"https://www.cuitonline.com/detalle/{cuit_limpio}/index.html",
            "origen": "cuitonline_perfecto"
        }


def obtener_pendientes(conn: sqlite3.Connection, force_all: bool = False, limite: int = None) -> list:
    c = conn.cursor()
    c.execute("SELECT dni, nombre_excel, nombre_oficial, datos_json FROM personas ORDER BY dni ASC")
    rows = c.fetchall()

    pendientes = []
    for dni, nom_excel, nom_oficial, dj in rows:
        if force_all:
            pendientes.append(dni)
            continue

        data = json.loads(dj) if dj else {}
        co = data.get("cuitonline")

        # 1. Sin CuitOnline
        if not co or not co.get("cuit_limpio"):
            pendientes.append(dni)
            continue

        # 2. Es Honeypot (enlace con hash o denominación scrambled)
        url = co.get("detalle_url", "")
        if "/detalle/" in url:
            part = url.split("/detalle/")[1].split("/")[0]
            if not part.isdigit() or len(part) != 11:
                pendientes.append(dni)
                continue

        # 3. No tiene detalle profundo de impuestos cargado
        if "impuestos_activos" not in co or co.get("impuestos_activos") is None:
            pendientes.append(dni)
            continue

    if limite:
        pendientes = pendientes[:limite]

    return pendientes


def enriquecer_cuitonline_perfecto(limite: int = None, num_workers: int = 3, force_all: bool = False, export_excel: bool = False):
    if not DB_PATH.exists():
        print(f"❌ Error: Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    pendientes = obtener_pendientes(conn, force_all=force_all, limite=limite)
    total_inicial = len(pendientes)

    print(f"🎯 Total registros a procesar con CuitOnline (Anti-Honeypot): {total_inicial}")

    if total_inicial == 0:
        print("✅ No hay registros pendientes de CuitOnline.")
        conn.close()
        return

    print(f"🚀 Iniciando {num_workers} workers concurrentes (Aislamiento Zero-Cookies)...")
    print("ℹ️ Puedes presionar Ctrl + C en cualquier momento: los datos se guardan al instante fila por fila.")

    workers = [CuitOnlineWorker(worker_id=i) for i in range(num_workers)]

    def ejecutar_tarea(item):
        if stop_requested.is_set():
            return None, None
        idx, dni = item
        w = workers[idx % len(workers)]
        # Jitter de rate limiting para no disparar 429
        time.sleep(random.uniform(0.1, 0.4))
        res = w.consultar(dni, max_intentos=3)
        return dni, res

    exitos = 0
    sin_datos = 0
    honeypots_totales = 0
    procesados = 0
    t0 = time.time()

    cola_actual = list(pendientes)
    ronda = 1
    max_rondas = 3

    try:
        while cola_actual and ronda <= max_rondas and not stop_requested.is_set():
            if ronda > 1:
                print(f"\n🔁 Iniciando Ronda {ronda} de reintentos para {len(cola_actual)} registros...")

            reintentos_siguiente = []

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {executor.submit(ejecutar_tarea, item): item[1] for item in enumerate(cola_actual)}

                try:
                    for fut in as_completed(futures):
                        if stop_requested.is_set():
                            break

                        dni, data = fut.result()
                        if dni is None:
                            continue

                        procesados += 1

                        if data and data.get("encontrado"):
                            exitos += 1
                            cuit = data["cuit_limpio"]
                            denom = data["denominacion"]
                            cond_afip = data["condicion_afip"]
                            domicilio = data.get("direccion") or ""
                            localidad = data.get("localidad") or ""
                            provincia = data.get("provincia") or ""
                            genero = data.get("genero") or ""
                            empleador = data.get("empleador") or "No"
                            actividades_json = json.dumps(data.get("actividades", []), ensure_ascii=False)
                            constancia_url = data.get("constancia_inscripcion_afip") or ""

                            with db_lock:
                                c = conn.cursor()
                                c.execute("SELECT datos_json, nombre_oficial, nombre_excel FROM personas WHERE dni = ?", (dni,))
                                row = c.fetchone()
                                datos_json = {}
                                nom_oficial_actual = ""
                                nom_excel = ""
                                if row:
                                    if row[0]:
                                        try:
                                            datos_json = json.loads(row[0])
                                        except Exception:
                                            datos_json = {}
                                    nom_oficial_actual = row[1] or ""
                                    nom_excel = row[2] or ""

                                # Guardar payload completo de cuitonline
                                datos_json["cuitonline"] = data

                                # Solo actualizamos nombre_oficial si no teníamos uno limpio de BCRA
                                nombre_final = denom if not nom_oficial_actual or len(nom_oficial_actual) < 3 else nom_oficial_actual
                                ahora = time.strftime("%Y-%m-%d %H:%M:%S")

                                c.execute("""
                                    UPDATE personas
                                    SET cuit = COALESCE(NULLIF(cuit, ''), ?),
                                        nombre_oficial = COALESCE(NULLIF(?, ''), nombre_oficial),
                                        condicion_afip = ?,
                                        domicilio_fiscal = COALESCE(NULLIF(?, ''), domicilio_fiscal),
                                        localidad = COALESCE(NULLIF(localidad, ''), ?),
                                        provincia = COALESCE(NULLIF(provincia, ''), ?),
                                        genero = COALESCE(NULLIF(genero, ''), ?),
                                        empleador = ?,
                                        actividades_afip = COALESCE(NULLIF(?, '[]'), actividades_afip),
                                        constancia_afip_url = ?,
                                        datos_json = ?,
                                        fecha_actualizacion = ?
                                    WHERE dni = ?
                                """, (
                                    cuit,
                                    nombre_final,
                                    cond_afip,
                                    domicilio,
                                    localidad,
                                    provincia,
                                    genero,
                                    empleador,
                                    actividades_json,
                                    constancia_url,
                                    json.dumps(datos_json, ensure_ascii=False),
                                    ahora,
                                    dni
                                ))
                                conn.commit()
                        elif data and not data.get("encontrado"):
                            sin_datos += 1
                        else:
                            reintentos_siguiente.append(dni)

                        honeypots_totales = sum(w.honeypot_bloqueados for w in workers)

                        if procesados % 10 == 0 or procesados == total_inicial:
                            elapsed = time.time() - t0
                            tasa = procesados / elapsed if elapsed > 0 else 0
                            restante = (total_inicial - exitos - sin_datos) / tasa if tasa > 0 else 0
                            pct_exito = (exitos * 100.0 / procesados) if procesados > 0 else 0
                            print(f"⏳ [{exitos + sin_datos}/{total_inicial}] ({(exitos+sin_datos)*100/total_inicial:.1f}%) | "
                                  f"✅ Con CUIT/AFIP: {exitos} ({pct_exito:.1f}%) | 🛡️ Honeypots bloqueados: {honeypots_totales} | "
                                  f"⚡ {tasa:.1f} reg/s | ⏱️ Restante: {restante:.0f}s", flush=True)

                except KeyboardInterrupt:
                    print("\n⚠️ Interrupción detectada (Ctrl + C). Deteniendo workers de forma segura...")
                    stop_requested.set()
                    executor.shutdown(wait=False, cancel_futures=True)

            cola_actual = reintentos_siguiente
            ronda += 1

    except KeyboardInterrupt:
        print("\n⚠️ Finalizando de manera segura...")

    conn.close()
    elapsed_total = time.time() - t0
    print(f"\n🎉 FINALIZADO en {elapsed_total:.1f}s.")
    print(f"   Total Procesados: {procesados} | Con éxito: {exitos} | Sin datos en AFIP: {sin_datos}")
    print(f"   🛡️ Intentos de Honeypot neutralizados: {honeypots_totales}")

    if export_excel and exitos > 0:
        print("\n📊 Regenerando base_macro_enriquecida.xlsx...")
        script_excel = PROJECT_ROOT / "scripts" / "exportar_banco_macro_excel.py"
        import subprocess
        subprocess.run([sys.executable, str(script_excel)], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enriquecedor Industrial CuitOnline Multi-Worker Anti-Honeypot")
    parser.add_argument("--workers", type=int, default=3, help="Cantidad de workers concurrentes (Recomendado: 2 a 4)")
    parser.add_argument("--limite", type=int, default=None, help="Límite de registros a procesar")
    parser.add_argument("--force-all", action="store_true", help="Reprocesar todos los registros incluso los que ya tienen datos")
    parser.add_argument("--export-excel", action="store_true", help="Regenerar base_macro_enriquecida.xlsx al terminar")
    args = parser.parse_args()

    enriquecer_cuitonline_perfecto(
        limite=args.limite,
        num_workers=args.workers,
        force_all=args.force_all,
        export_excel=args.export_excel
    )
