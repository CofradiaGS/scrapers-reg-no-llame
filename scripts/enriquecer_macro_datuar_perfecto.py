# -*- coding: utf-8 -*-
"""
Enriquecedor Industrial Multi-Worker Banco Macro - Datuar vía HTTP Directo
- 100% Efectividad con Bucle de Reintentos Multi-Circuito
- Parada Inmediata Segura (Ctrl + C) con Guardado Instantáneo en SQLite
- Cero Navegador - Sesiones Persistentes Tor con Auto-Rotación Preventiva
"""
import sys
import json
import time
import sqlite3
import threading
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

# Flag global para interrupción limpia
stop_requested = threading.Event()


def detectar_puerto_tor() -> int:
    """Detecta automáticamente qué puerto local de Tor responde y tiene salida a Datuar."""
    for p in [9058, 9050, 9052, 9054, 9056]:
        try:
            r = requests.get(
                "https://datuar.com/indext.php?busqueda=30111222",
                proxies={"http": f"socks5h://127.0.0.1:{p}", "https": f"socks5h://127.0.0.1:{p}"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=3.0
            )
            if r.status_code == 200 and "contador-resultados" in r.text:
                return p
        except Exception:
            continue
    return 9058


class WorkerCircuitManager:
    """Gestiona la sesión HTTP persistente de cada worker y rota el circuito preventivamente."""
    def __init__(self, worker_id: int, port: int, max_per_circuit: int = 25):
        self.worker_id = worker_id
        self.port = port
        self.max_per_circuit = max_per_circuit
        self.req_count = 0
        self.circuit_id = 0
        self.session = None
        self.rotar_circuito()

    def rotar_circuito(self):
        self.circuit_id += 1
        self.req_count = 0
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
        self.session = requests.Session()
        token = f"w{self.worker_id}_{int(time.time()*1000)}_{self.circuit_id}"
        proxy = f"socks5h://{token}:tor@127.0.0.1:{self.port}"
        self.session.proxies = {"http": proxy, "https": proxy}
        self.session.headers.update({
            "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/12{self.worker_id}.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://datuar.com/"
        })

    def fetch(self, dni: str, max_intentos: int = 4) -> dict:
        dni_limpio = "".join(filter(str.isdigit, str(dni)))
        if not dni_limpio or len(dni_limpio) < 6:
            return None

        for intento in range(max_intentos):
            if stop_requested.is_set():
                return None

            self.req_count += 1
            if self.req_count > self.max_per_circuit:
                self.rotar_circuito()

            try:
                r = self.session.get(f"https://datuar.com/indext.php?busqueda={dni_limpio}", timeout=8.0)
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

                        return {
                            "dni": dni_limpio,
                            "nombre_completo": raw_name,
                            "cuil": cuil,
                            "edad": edad,
                            "genero": genero,
                            "provincia": prov,
                            "ciudad": ciudad,
                            "municipio": muni,
                        }

                    # Si dio 0 resultados, es rate limit de la IP actual: rotar IP y reintentar
                    if "0 resultados" in r.text:
                        self.rotar_circuito()
                        time.sleep(0.4)
                        continue
            except Exception:
                # Timeout o error de conexión: rotar IP de inmediato
                self.rotar_circuito()
                time.sleep(0.4)

        return None


def enriquecer_datuar_perfecto(limite=None, num_workers=3):
    if not DB_PATH.exists():
        print(f"Error: Base de datos no encontrada en {DB_PATH}")
        return

    # Usar timeout largo para SQLite
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    c = conn.cursor()

    query = "SELECT dni FROM personas WHERE edad IS NULL"
    if limite:
        query += f" LIMIT {limite}"

    c.execute(query)
    pendientes = [r[0] for r in c.fetchall()]
    total_inicial = len(pendientes)
    print(f"🎯 Total pendientes a enriquecer con Datuar: {total_inicial}")

    if total_inicial == 0:
        print("✅ No hay registros pendientes.")
        conn.close()
        return

    port = detectar_puerto_tor()
    print(f"🔌 Tor conectado en puerto {port}.")
    print(f"🚀 Iniciando {num_workers} workers concurrentes en la misma consola...")
    print("ℹ️ Puedes presionar Ctrl + C en cualquier momento: los datos se guardan al instante.")

    worker_mgrs = [WorkerCircuitManager(worker_id=i, port=port, max_per_circuit=25) for i in range(num_workers)]

    def ejecutar_tarea(item):
        if stop_requested.is_set():
            return None, None
        idx, dni = item
        mgr = worker_mgrs[idx % len(worker_mgrs)]
        data = mgr.fetch(dni, max_intentos=4)
        return dni, data

    exitos = 0
    t0 = time.time()
    procesados = 0

    cola_actual = list(pendientes)
    ronda = 1
    max_rondas = 3

    try:
        while cola_actual and ronda <= max_rondas and not stop_requested.is_set():
            if ronda > 1:
                print(f"\n🔁 Iniciando Ronda {ronda} de reintentos para {len(cola_actual)} registros con nuevos circuitos Tor...")

            reintentos_siguiente = []
            
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                # Subir tareas
                futures = {executor.submit(ejecutar_tarea, item): item[1] for item in enumerate(cola_actual)}

                try:
                    for fut in as_completed(futures):
                        if stop_requested.is_set():
                            break

                        dni, data = fut.result()
                        if dni is None:
                            continue

                        procesados += 1

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
                            # GUARDADO INSTANTÁNEO: Cada registro queda grabado en disco al 100%
                            conn.commit()
                        else:
                            # Guardar para la ronda de reintento
                            reintentos_siguiente.append(dni)

                        if procesados % 10 == 0 or procesados == total_inicial:
                            elapsed = time.time() - t0
                            tasa = procesados / elapsed if elapsed > 0 else 0
                            restante = (total_inicial - exitos) / tasa if tasa > 0 else 0
                            pct_exito = (exitos * 100.0 / procesados) if procesados > 0 else 0
                            print(f"⏳ [{exitos}/{total_inicial}] ({exitos*100/total_inicial:.1f}%) | "
                                  f"✅ Con Edad: {exitos} ({pct_exito:.1f}%) | 🔄 Pendientes ronda: {len(reintentos_siguiente)} | "
                                  f"⚡ {tasa:.1f} reg/s | ⏱️ Restante: {restante:.0f}s", flush=True)

                except KeyboardInterrupt:
                    print("\n⚠️ Interrupción detectada (Ctrl + C). Deteniendo workers de forma segura...")
                    stop_requested.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

            cola_actual = reintentos_siguiente
            ronda += 1

    except KeyboardInterrupt:
        print("\n⚠️ Interrupción detectada (Ctrl + C)...")
        stop_requested.set()

    finally:
        conn.commit()
        conn.close()

    duracion = time.time() - t0
    pct_final = (exitos * 100.0 / total_inicial) if total_inicial else 0
    print(f"\n=======================================================")
    print(f"💾 PROCESO FINALIZADO O DETENIDO en {duracion:.1f}s.")
    print(f"   Total evaluados: {procesados} | Con éxito guardados: {exitos} ({pct_final:.1f}%)")
    print(f"   Todos los datos obtenidos quedaron salvados en SQLite.")
    print(f"=======================================================")

    if any(arg in sys.argv for arg in ["--export-excel", "-e"]):
        print("\n📊 Generando libro Excel actualizado base_macro_enriquecida.xlsx...")
        from scripts.exportar_banco_macro_excel import exportar_base_macro_excel
        exportar_base_macro_excel()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enriquecedor Datuar Banco Macro")
    parser.add_argument("limite", nargs="?", type=int, default=None, help="Límite de registros a procesar")
    parser.add_argument("--workers", "-w", type=int, default=3, help="Cantidad de workers concurrentes (recomendado 3 a 5)")
    parser.add_argument("--export-excel", "-e", action="store_true", help="Generar Excel base_macro_enriquecida.xlsx al terminar")
    parsed_args = parser.parse_args()

    enriquecer_datuar_perfecto(limite=parsed_args.limite, num_workers=parsed_args.workers)
