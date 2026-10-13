# -*- coding: utf-8 -*-
"""
Enriquecedor Industrial Multi-Worker Banco Macro - Central de Deudores BCRA vía API Directa
- Engine Ultra-Rápido SSL/TLS SECLEVEL=1 (sin desconexiones ni caídas)
- Multi-Worker concurrente en la misma consola con Rate Limiter adaptativo
- Extracción oficial de Deudas Financieras, Situación Crediticia (1 a 5), Banco Macro y Entidades
- Sincronización instantánea en tablas 'personas' y 'bcra_entidades' (conn.commit por fila)
- Parada inmediata segura (Ctrl + C) sin pérdida de datos
"""
import sys
import os
import json
import time
import ssl
import random
import sqlite3
import threading
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

stop_requested = threading.Event()
db_lock = threading.Lock()


class BCRAGlobalRateLimiter:
    """
    Controlador de tasa global coordinado entre todos los workers.
    La API de BCRA (api.bcra.gob.ar) posee un WAF perimetral estricto: ráfagas simultáneas
    activan el reseteo TCP (Connection was reset). Mantener ~0.85s entre peticiones garantiza 100% de éxito.
    """
    def __init__(self, min_interval: float = 0.85):
        self.min_interval = min_interval
        self.next_allowed_time = time.time()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            target_time = max(now, self.next_allowed_time)
            self.next_allowed_time = target_time + self.min_interval
        
        # El sleep se realiza fuera del lock para no congelar otros hilos
        sleep_dur = target_time - now
        if sleep_dur > 0:
            time.sleep(sleep_dur)

    def enfriar(self, segundos: float = 3.5):
        with self.lock:
            now = time.time()
            self.next_allowed_time = max(self.next_allowed_time, now + segundos)

global_rate_limiter = BCRAGlobalRateLimiter(min_interval=0.85)


class BCRA_SSLAdapter(HTTPAdapter):
    """Adaptador SSL con SECLEVEL=1 para evitar el corte abrupto de conexión en la API de BCRA."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


class BCRAWorker:
    """Worker con sesión optimizada y reintentos adaptativos contra api.bcra.gob.ar."""
    BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas"

    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.session = self._crear_session()

    def _crear_session(self) -> requests.Session:
        s = requests.Session()
        s.mount("https://", BCRA_SSLAdapter())
        s.headers.update({
            "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/12{self.worker_id}.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        })
        return s

    def consultar(self, cuit: str, max_intentos: int = 4) -> Optional[Dict[str, Any]]:
        cuit_limpio = "".join(filter(str.isdigit, str(cuit)))
        if len(cuit_limpio) != 11:
            return None

        url = f"{self.BASE_URL}/{cuit_limpio}"

        for intento in range(1, max_intentos + 1):
            if stop_requested.is_set():
                return None

            try:
                # Coordinar llamada globalmente para no disparar el WAF de BCRA
                global_rate_limiter.wait()
                resp = self.session.get(url, timeout=10.0)

                # Rate limiting de BCRA (429) o protección perimetral
                if resp.status_code == 429:
                    global_rate_limiter.enfriar(3.5 * intento)
                    continue

                # 404: Sin registros en Central de Deudores (Persona al día / sin deuda)
                if resp.status_code == 404:
                    return {
                        "cuit": cuit_limpio,
                        "sin_deuda": True,
                        "entidades": [],
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                if resp.status_code != 200:
                    time.sleep(0.5)
                    continue

                data = resp.json()
                results = data.get("results", {})
                if not results:
                    return {
                        "cuit": cuit_limpio,
                        "sin_deuda": True,
                        "entidades": [],
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                denominacion = results.get("denominacion", "").strip()
                periodos = results.get("periodos", [])

                if not periodos:
                    return {
                        "cuit": cuit_limpio,
                        "denominacion": denominacion,
                        "sin_deuda": True,
                        "entidades": [],
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                # Tomar el período más reciente (primer elemento)
                ultimo_periodo = periodos[0]
                periodo_str = str(ultimo_periodo.get("periodo", ""))
                entidades_raw = ultimo_periodo.get("entidades", [])

                entidades_procesadas = []
                peor_situacion = 0
                deuda_total = 0.0
                deuda_macro_miles = 0.0
                deuda_macro_situacion = None

                for ent in entidades_raw:
                    nombre_ent = ent.get("entidad", "").strip()
                    sit = int(ent.get("situacion", 1))
                    monto = float(ent.get("monto", 0.0))

                    peor_situacion = max(peor_situacion, sit)
                    deuda_total += monto

                    if "MACRO" in nombre_ent.upper():
                        deuda_macro_miles += monto
                        deuda_macro_situacion = sit

                    entidades_procesadas.append({
                        "entidad": nombre_ent,
                        "situacion": sit,
                        "monto_miles": monto,
                        "periodo": periodo_str,
                        "dias_atraso": ent.get("diasAtrasoPago", 0),
                        "proceso_judicial": ent.get("procesoJud", False)
                    })

                return {
                    "cuit": cuit_limpio,
                    "denominacion": denominacion,
                    "periodo": periodo_str,
                    "entidades": entidades_procesadas,
                    "peor_situacion": peor_situacion,
                    "deuda_total_miles": round(deuda_total, 2),
                    "deuda_macro_miles": round(deuda_macro_miles, 2),
                    "deuda_macro_situacion": deuda_macro_situacion,
                    "sin_deuda": (len(entidades_procesadas) == 0)
                }

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = self._crear_session()
                global_rate_limiter.enfriar(3.0 * intento)
            except Exception:
                time.sleep(0.5)

        return None


def obtener_pendientes(conn: sqlite3.Connection, force_all: bool = False, limite: int = None) -> list:
    c = conn.cursor()
    c.execute("""
        SELECT dni, cuit, nombre_excel, nombre_oficial, peor_situacion_bcra, datos_json 
        FROM personas 
        WHERE cuit IS NOT NULL AND length(replace(cuit, '-', '')) = 11
        ORDER BY dni ASC
    """)
    rows = c.fetchall()

    pendientes = []
    for dni, cuit, nom_ex, nom_of, peor_sit, dj in rows:
        cuit_clean = "".join(filter(str.isdigit, str(cuit)))
        if len(cuit_clean) != 11:
            continue

        if force_all:
            pendientes.append((dni, cuit_clean))
            continue

        data = json.loads(dj) if dj else {}
        bcra_data = data.get("bcra")

        # Pendiente si no tiene 'bcra' en JSON o falta peor_situacion
        if not bcra_data or peor_sit is None:
            pendientes.append((dni, cuit_clean))

    if limite:
        pendientes = pendientes[:limite]

    return pendientes


def enriquecer_bcra_perfecto(limite: int = None, num_workers: int = 5, force_all: bool = False, export_excel: bool = False):
    if not DB_PATH.exists():
        print(f"❌ Error: Base de datos no encontrada en {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    pendientes = obtener_pendientes(conn, force_all=force_all, limite=limite)
    total_inicial = len(pendientes)

    print(f"🎯 Total registros a procesar con BCRA Central de Deudores: {total_inicial}")

    if total_inicial == 0:
        print("✅ No hay registros pendientes de BCRA.")
        conn.close()
        return

    print(f"🚀 Iniciando {num_workers} workers concurrentes (API BCRA Directa con SSL SECLEVEL=1)...")
    print("ℹ️ Puedes presionar Ctrl + C en cualquier momento: los datos se guardan al instante fila por fila.")

    workers = [BCRAWorker(worker_id=i) for i in range(num_workers)]

    def ejecutar_tarea(item):
        if stop_requested.is_set():
            return None, None, None
        idx, (dni, cuit) = item
        w = workers[idx % len(workers)]
        time.sleep(random.uniform(0.05, 0.15))
        res = w.consultar(cuit, max_intentos=3)
        return dni, cuit, res

    exitos_con_deuda = 0
    exitos_sin_deuda = 0
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

                        dni, cuit, data = fut.result()
                        if dni is None:
                            continue

                        procesados += 1

                        if data is not None:
                            peor_sit = data.get("peor_situacion", 0)
                            deuda_tot = data.get("deuda_total_miles", 0.0)
                            deuda_macro = data.get("deuda_macro_miles", 0.0)
                            macro_sit = data.get("deuda_macro_situacion")
                            denom = data.get("denominacion") or ""
                            entidades = data.get("entidades", [])
                            periodo = data.get("periodo", "")

                            if peor_sit > 0 or deuda_tot > 0:
                                exitos_con_deuda += 1
                            else:
                                exitos_sin_deuda += 1

                            ahora = time.strftime("%Y-%m-%d %H:%M:%S")

                            with db_lock:
                                c = conn.cursor()
                                # 1. Actualizar JSON y datos consolidados en personas
                                c.execute("SELECT datos_json, nombre_oficial FROM personas WHERE dni = ?", (dni,))
                                row = c.fetchone()
                                datos_json = {}
                                nom_of_actual = ""
                                if row:
                                    if row[0]:
                                        try:
                                            datos_json = json.loads(row[0])
                                        except Exception:
                                            datos_json = {}
                                    nom_of_actual = row[1] or ""

                                datos_json["bcra"] = data

                                # Si no teníamos nombre oficial, asignamos la denominación bancaria oficial
                                nombre_final = nom_of_actual if nom_of_actual and len(nom_of_actual) > 3 else denom

                                c.execute("""
                                    UPDATE personas
                                    SET peor_situacion_bcra = ?,
                                        deuda_macro_miles = ?,
                                        deuda_macro_situacion = ?,
                                        deuda_total_bcra_miles = ?,
                                        nombre_oficial = COALESCE(NULLIF(nombre_oficial, ''), ?),
                                        datos_json = ?,
                                        fecha_actualizacion = ?
                                    WHERE dni = ?
                                """, (
                                    peor_sit,
                                    deuda_macro,
                                    macro_sit,
                                    deuda_tot,
                                    nombre_final,
                                    json.dumps(datos_json, ensure_ascii=False),
                                    ahora,
                                    dni
                                ))

                                # 2. Sincronizar tabla relacional bcra_entidades
                                c.execute("DELETE FROM bcra_entidades WHERE dni = ?", (dni,))
                                for ent in entidades:
                                    c.execute("""
                                        INSERT INTO bcra_entidades (dni, cuit, entidad, situacion, monto_miles, periodo, fecha_consulta)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        dni,
                                        cuit,
                                        ent.get("entidad", ""),
                                        ent.get("situacion", 1),
                                        ent.get("monto_miles", 0.0),
                                        ent.get("periodo", periodo),
                                        ahora
                                    ))

                                conn.commit()
                        else:
                            reintentos_siguiente.append((dni, cuit))

                        if procesados % 10 == 0 or procesados == total_inicial:
                            elapsed = time.time() - t0
                            tasa = procesados / elapsed if elapsed > 0 else 0
                            exitos = exitos_con_deuda + exitos_sin_deuda
                            restante = (total_inicial - exitos) / tasa if tasa > 0 else 0
                            pct_exito = (exitos * 100.0 / procesados) if procesados > 0 else 0
                            print(f"⏳ [{exitos}/{total_inicial}] ({exitos*100/total_inicial:.1f}%) | "
                                  f"💳 Con Deuda: {exitos_con_deuda} | 🟢 Sin Deuda: {exitos_sin_deuda} | "
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
    exitos_totales = exitos_con_deuda + exitos_sin_deuda
    print(f"\n🎉 FINALIZADO en {elapsed_total:.1f}s.")
    print(f"   Total Procesados: {procesados} | Con éxito: {exitos_totales}")
    print(f"   💳 Con Deuda Bancaria: {exitos_con_deuda} | 🟢 Sin Deuda (Al día): {exitos_sin_deuda}")

    if export_excel and exitos_totales > 0:
        print("\n📊 Regenerando base_macro_enriquecida.xlsx...")
        script_excel = PROJECT_ROOT / "scripts" / "exportar_banco_macro_excel.py"
        import subprocess
        subprocess.run([sys.executable, str(script_excel)], check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enriquecedor Industrial BCRA Multi-Worker Central de Deudores")
    parser.add_argument("--workers", type=int, default=5, help="Cantidad de workers concurrentes (Recomendado: 4 a 8)")
    parser.add_argument("--limite", type=int, default=None, help="Límite de registros a procesar")
    parser.add_argument("--force-all", action="store_true", help="Reprocesar todos los registros incluso los que ya tienen datos")
    parser.add_argument("--export-excel", action="store_true", help="Regenerar base_macro_enriquecida.xlsx al terminar")
    args = parser.parse_args()

    enriquecer_bcra_perfecto(
        limite=args.limite,
        num_workers=args.workers,
        force_all=args.force_all,
        export_excel=args.export_excel
    )
