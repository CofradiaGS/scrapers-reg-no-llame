# -*- coding: utf-8 -*-
"""
Orquestador Industrial: Enriquecimiento Total Banco Macro a SQLite
Ejecuta el pipeline completo de scrapers para los 4.553 DNIs de base_macro.xlsx:
1. CuitOnline (CUIT, AFIP, Domicilio Fiscal, Actividades)
2. BCRA (Central de Deudores: Deuda Banco Macro, Situación 1-5, Cheques)
3. Datuar (Demografía)
4. IRIS Movistar (Búsqueda por DNI con extracción multi-lupa de todas las operaciones y líneas)
5. Telcos Cobro Express (Claro, Personal, Movistar para todas las líneas descubiertas)
"""
import os
import sys
import time
import json
import sqlite3
import argparse
import logging
import signal
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import threading
import queue
import concurrent.futures
import pandas as pd

# Reconfigurar stdout en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.cuitonline.cuitonline_adapter import CuitOnlineAdapter
from adapters.scrapers.bcra.bcra_client import BCRAClient
from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from adapters.scrapers.claro.claro_adapter import ClaroAdapter
from adapters.scrapers.personal.personal_adapter import PersonalAdapter
from adapters.scrapers.movistar.movistar_adapter import MovistarAdapter

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"
LOG_FILE = PROJECT_ROOT / "procesar_banco_macro.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("MacroOrchestrator")


import queue
import concurrent.futures

class TelcoWorker:
    """Instancia de auditoría de Telcos asociada a un slot de Tor específico."""
    def __init__(self, slot: int):
        self.slot = slot
        self.claro = ClaroAdapter(worker_slot=slot)
        self.personal = PersonalAdapter(worker_slot=slot)
        self.movistar = MovistarAdapter(worker_slot=slot)
        self.iniciado = False

    def iniciar(self):
        if not self.iniciado:
            self.claro.iniciar()
            self.personal.iniciar()
            self.movistar.iniciar()
            self.iniciado = True

    def cerrar(self):
        if self.iniciado:
            self.claro.cerrar()
            self.personal.cerrar()
            self.movistar.cerrar()
            self.iniciado = False

    def auditar_linea(self, ani: str, dni: str) -> dict:
        self.iniciar()
        linea_obj = Linea(ani=ani, dni=dni)
        operador_activo = None
        deuda_linea = 0.0
        info_linea = {}

        # 1. Claro
        try:
            res_c = self.claro.consultar_linea(linea_obj)
            if res_c.status == StatusScraping.COINCIDENCIA:
                operador_activo = "Claro"
                deuda_linea = float(res_c.detalles.get("deuda_total", 0.0))
                info_linea = res_c.detalles
        except Exception as e_c:
            logger.debug(f"[Slot {self.slot}] Aviso Claro {ani}: {e_c}")

        # 2. Personal (si Claro no dio o para enriquecer)
        if not operador_activo:
            try:
                res_p = self.personal.consultar_linea(linea_obj)
                if res_p.status == StatusScraping.COINCIDENCIA:
                    operador_activo = "Personal"
                    deuda_linea = float(res_p.detalles.get("deuda_total", 0.0))
                    info_linea = res_p.detalles
            except Exception as e_p:
                logger.debug(f"[Slot {self.slot}] Aviso Personal {ani}: {e_p}")

        # 3. Movistar (si no dio Claro ni Personal)
        if not operador_activo:
            try:
                res_m = self.movistar.consultar_linea(linea_obj)
                if res_m.status == StatusScraping.COINCIDENCIA:
                    operador_activo = "Movistar"
                    deuda_linea = float(res_m.detalles.get("deuda_total", 0.0))
                    info_linea = res_m.detalles
            except Exception as e_m:
                logger.debug(f"[Slot {self.slot}] Aviso Movistar {ani}: {e_m}")

        return {
            "ani": ani,
            "dni": dni,
            "operador": operador_activo or "Sin Coincidencia Telco",
            "tiene_deuda": deuda_linea > 0,
            "deuda_total": deuda_linea,
            "detalles_json": json.dumps(info_linea, ensure_ascii=False),
            "operador_activo": operador_activo
        }


class TelcoWorkerPool:
    """Pool de workers concurrentes de Telcos con rotación Stream Isolation de Tor."""
    def __init__(self, num_workers: int = 15, orchestrator: Optional[Any] = None):
        self.num_workers = max(1, num_workers)
        self.orchestrator = orchestrator
        self.queue: queue.Queue = queue.Queue()
        self.workers = [TelcoWorker(slot=i) for i in range(1, self.num_workers + 1)]
        for w in self.workers:
            self.queue.put(w)
        logger.info(f"TelcoWorkerPool inicializado con {self.num_workers} slots concurrentes.")

    def auditar_linea_pooled(self, ani: str, dni: str) -> dict:
        if self.orchestrator and self.orchestrator.stop_event.is_set():
            return {
                "ani": ani,
                "dni": dni,
                "operador": "Cancelado",
                "tiene_deuda": False,
                "deuda_total": 0.0,
                "detalles_json": "{}",
                "operador_activo": None
            }
        worker = self.queue.get()
        try:
            return worker.auditar_linea(ani, dni)
        finally:
            self.queue.put(worker)

    def auditar_lineas_paralelo(self, lineas: list, dni: str) -> list:
        if not lineas or (self.orchestrator and self.orchestrator.stop_event.is_set()):
            return []
        max_hilos = min(len(lineas), self.num_workers)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_hilos)
        try:
            futures = [executor.submit(self.auditar_linea_pooled, ani, dni) for ani in lineas]
            resultados = []
            for f in concurrent.futures.as_completed(futures):
                if self.orchestrator and self.orchestrator.stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                resultados.append(f.result())
            return resultados
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def cerrar(self):
        for w in self.workers:
            w.cerrar()


class MacroEnricherWorker:
    """Worker concurrente que encapsula adaptadores y una sesión HTTP independiente de IRIS."""
    def __init__(self, worker_id: int, orchestrator: "BancoMacroOrchestrator"):
        self.worker_id = worker_id
        self.orchestrator = orchestrator
        self.cuitonline = CuitOnlineAdapter()
        self.bcra = BCRAClient()
        self.datuar = DatuarAdapter()

        if not orchestrator.skip_iris:
            self.iris = IrisHttpAdapter(forzar_horario=orchestrator.forzar_horario_iris)
        else:
            self.iris = None

    def iniciar(self):
        if self.iris:
            self.iris.iniciar()
            try:
                self.iris.autenticar()
            except Exception as e:
                logger.warning(f"[W{self.worker_id}] Aviso al autenticar IRIS: {e}")

    def cerrar(self):
        if self.cuitonline:
            self.cuitonline.cerrar()
        if self.datuar:
            self.datuar.cerrar()
        if self.iris:
            self.iris.cerrar()

    def procesar_persona(self, dni: str, nombre_excel: str) -> Dict[str, Any]:
        prefix = f"[W{self.worker_id}] [{dni}]"
        resumen: Dict[str, Any] = {
            "dni": dni,
            "nombre_excel": nombre_excel,
            "cuit": None,
            "lineas_descubiertas": [],
            "telcos_resultados": []
        }

        if self.orchestrator.stop_event.is_set():
            return resumen

        # 1. CuitOnline (con Rate Limiter coordinado entre workers)
        logger.info(f"{prefix} Paso 1/5: Consultando CuitOnline...")
        cuit_encontrado = None
        cuit_data = {}
        try:
            with self.orchestrator.cuitonline_lock:
                ahora = time.time()
                transcurrido = ahora - self.orchestrator.last_cuitonline_time
                if transcurrido < 1.0:
                    time.sleep(1.0 - transcurrido)
                if self.orchestrator.stop_event.is_set():
                    return resumen
                res_cuit = self.cuitonline.consultar_linea(Linea(ani="", dni=dni))
                self.orchestrator.last_cuitonline_time = time.time()

            if res_cuit.status == StatusScraping.COINCIDENCIA:
                cuit_data = res_cuit.detalles or {}
                raw_cuit = cuit_data.get("cuit_limpio")
                clean_cuit = "".join(filter(str.isdigit, str(raw_cuit)))
                if len(clean_cuit) == 11:
                    cuit_encontrado = clean_cuit
                    resumen["cuit"] = cuit_encontrado
                    logger.info(f"{prefix} CuitOnline OK: CUIT {cuit_encontrado} - {cuit_data.get('denominacion')}")
                else:
                    logger.warning(f"{prefix} CuitOnline: CUIT no válido ({raw_cuit}), se buscará en Datuar")
        except Exception as e:
            logger.warning(f"{prefix} Error en CuitOnline: {e}")

        if self.orchestrator.stop_event.is_set():
            return resumen

        # 2. Datuar (Consulta Obligatoria para TODOS los registros)
        datuar_data = {}
        logger.info(f"{prefix} Paso 2/5: Consultando Datuar...")
        try:
            res_datuar = self.datuar.consultar_linea(Linea(ani="", dni=dni))
            if res_datuar.status == StatusScraping.COINCIDENCIA:
                datuar_data = res_datuar.detalles or {}
                raw_cuil = datuar_data.get("cuil")
                clean_cuil = "".join(filter(str.isdigit, str(raw_cuil)))
                if not cuit_encontrado and len(clean_cuil) == 11:
                    cuit_encontrado = clean_cuil
                    resumen["cuit"] = cuit_encontrado
                    logger.info(f"{prefix} Datuar: CUIT/CUIL obtenido {cuit_encontrado} - {datuar_data.get('nombre_completo')}")
                else:
                    logger.info(f"{prefix} Datuar OK: {datuar_data.get('nombre_completo')} | Edad: {datuar_data.get('edad')} | Prov: {datuar_data.get('provincia')}")
            else:
                logger.info(f"{prefix} Datuar: Sin registros para DNI {dni}")
        except Exception as e:
            logger.debug(f"{prefix} Aviso en Datuar: {e}")

        if self.orchestrator.stop_event.is_set():
            return resumen

        # 3. BCRA Central de Deudores (Utiliza CUIT consolidado de CuitOnline o Datuar)
        bcra_data = {}
        if cuit_encontrado:
            logger.info(f"{prefix} Paso 3/5: Consultando BCRA con CUIT {cuit_encontrado}...")
            try:
                bcra_res = self.bcra.consultar_deuda(cuit_encontrado)
                if bcra_res:
                    bcra_data = bcra_res
                    deuda_macro = bcra_res.get("deuda_macro_miles", 0.0)
                    sit_macro = bcra_res.get("deuda_macro_situacion")
                    logger.info(f"{prefix} BCRA OK: Deuda Macro ${deuda_macro}k (Sit {sit_macro}) | Total ${bcra_res.get('deuda_total_miles')}k (Peor Sit {bcra_res.get('peor_situacion')})")
            except Exception as e:
                logger.warning(f"{prefix} Error en BCRA: {e}")
        else:
            logger.info(f"{prefix} Paso 3/5: Omitiendo BCRA (sin CUIT verificado en CuitOnline ni Datuar)")

        if self.orchestrator.stop_event.is_set():
            return resumen

        # 4. IRIS Movistar (Sesión HTTP propia sin Tor)
        lineas_descubiertas = []
        operaciones_iris = []
        error_iris = None
        if self.iris and not self.orchestrator.skip_iris:
            logger.info(f"{prefix} Paso 4/5: Consultando IRIS por DNI...")
            try:
                res_iris = self.iris.consultar_por_dni(dni)
                if res_iris.status == StatusScraping.COINCIDENCIA:
                    det_iris = res_iris.detalles or {}
                    lineas_descubiertas = det_iris.get("lineas_descubiertas") or []
                    operaciones_iris = det_iris.get("registros_historicos") or []
                    resumen["lineas_descubiertas"] = lineas_descubiertas
                    logger.info(f"{prefix} IRIS OK: {len(lineas_descubiertas)} líneas descubiertas en {len(operaciones_iris)} operaciones históricas")
                else:
                    logger.info(f"{prefix} IRIS: Sin operaciones registradas")
            except Exception as e:
                logger.warning(f"{prefix} Error en IRIS: {e}")
                error_iris = f"Error IRIS: {e}"
        else:
            logger.info(f"{prefix} Paso 4/5: IRIS omitido")

        if self.orchestrator.stop_event.is_set():
            return resumen

        # 5. Telcos Cobro Express: Claro + Personal + Movistar (Pool Concurrente en Tor)
        telcos_resultados = []
        lineas_activas_textos = []

        if self.orchestrator.telco_pool and not self.orchestrator.skip_telcos and lineas_descubiertas:
            num_hilos = min(len(lineas_descubiertas), self.orchestrator.telcos_workers)
            logger.info(f"{prefix} Paso 5/5: Auditando {len(lineas_descubiertas)} líneas en Telcos en paralelo ({num_hilos} workers)...")
            resultados_pool = self.orchestrator.telco_pool.auditar_lineas_paralelo(lineas_descubiertas, dni)
            for res_item in resultados_pool:
                telco_reg = {
                    "ani": res_item["ani"],
                    "dni": res_item["dni"],
                    "operador": res_item["operador"],
                    "tiene_deuda": res_item["tiene_deuda"],
                    "deuda_total": res_item["deuda_total"],
                    "detalles_json": res_item["detalles_json"]
                }
                telcos_resultados.append(telco_reg)

                if res_item.get("operador_activo"):
                    op_act = res_item["operador_activo"]
                    deuda = res_item["deuda_total"]
                    desc_txt = f"{res_item['ani']}: {op_act}"
                    if deuda > 0:
                        desc_txt += f" (${deuda:.2f})"
                    lineas_activas_textos.append(desc_txt)

            logger.info(f"{prefix} Telcos finalizado: {len(lineas_activas_textos)} líneas activas identificadas -> {lineas_activas_textos}")
        else:
            if not lineas_descubiertas:
                logger.info(f"{prefix} Paso 5/5: Sin líneas telefónicas para auditar en Telcos")

        if self.orchestrator.stop_event.is_set():
            logger.info(f"{prefix} Detención solicitada: Omitiendo persistencia de DNI {dni} para reanudación limpia.")
            return resumen

        # 6. Persistencia Atómica en SQLite (con Lock)
        self.orchestrator.persistir_resultado(
            dni=dni,
            cuit_data=cuit_data,
            bcra_data=bcra_data,
            datuar_data=datuar_data,
            operaciones_iris=operaciones_iris,
            lineas_descubiertas=lineas_descubiertas,
            telcos_resultados=telcos_resultados,
            lineas_activas_resumen=" | ".join(lineas_activas_textos),
            error_msg=error_iris
        )

        resumen["telcos_resultados"] = telcos_resultados
        return resumen


class BancoMacroOrchestrator:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        skip_iris: bool = False,
        skip_telcos: bool = False,
        forzar_horario_iris: bool = False,
        iris_workers: int = 5,
        telcos_workers: int = 15
    ):
        self.db_path = db_path
        self.skip_iris = skip_iris
        self.skip_telcos = skip_telcos
        self.forzar_horario_iris = forzar_horario_iris
        self.iris_workers = max(1, iris_workers)
        self.telcos_workers = max(1, telcos_workers)
        self.db_write_lock = threading.Lock()
        self.cuitonline_lock = threading.Lock()
        self.last_cuitonline_time = 0.0
        self.stop_event = threading.Event()

        # Telcos: Pool de N workers concurrentes (default 15) sobre Tor Stream Isolation
        if not self.skip_telcos:
            self.telco_pool = TelcoWorkerPool(num_workers=self.telcos_workers, orchestrator=self)
        else:
            self.telco_pool = None

    def get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def cerrar(self):
        """Cierra descriptores y sesiones de red."""
        self.stop_event.set()
        logger.info("Cerrando recursos del orquestador...")
        if self.telco_pool:
            self.telco_pool.cerrar()

    def procesar_persona(self, dni: str, nombre_excel: str = "Manual") -> Dict[str, Any]:
        """Procesa un único DNI para pruebas puntuales."""
        worker = MacroEnricherWorker(worker_id=1, orchestrator=self)
        try:
            worker.iniciar()
            return worker.procesar_persona(dni, nombre_excel)
        finally:
            worker.cerrar()

    def persistir_resultado(
        self,
        dni: str,
        cuit_data: Dict[str, Any],
        bcra_data: Dict[str, Any],
        datuar_data: Dict[str, Any],
        operaciones_iris: List[Dict[str, Any]],
        lineas_descubiertas: List[str],
        telcos_resultados: List[Dict[str, Any]],
        lineas_activas_resumen: str,
        error_msg: Optional[str] = None
    ):
        with self.db_write_lock:
            conn = self.get_db()
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                # 1. Actualizar tabla personas
                nombre_oficial = cuit_data.get("denominacion") or datuar_data.get("nombre_completo") or ""
                if nombre_oficial:
                    nombre_oficial = nombre_oficial.replace("?", "Ñ")

                cuit_raw = cuit_data.get("cuit_limpio") or datuar_data.get("cuil") or ""
                cuit_clean = "".join(filter(str.isdigit, str(cuit_raw)))
                cuit = cuit_clean if len(cuit_clean) == 11 else ""

                gen_raw = str(cuit_data.get("genero") or datuar_data.get("genero") or "").lower()
                if "masculin" in gen_raw or gen_raw == "m":
                    genero = "Masculino"
                elif "femenin" in gen_raw or gen_raw == "f":
                    genero = "Femenino"
                else:
                    genero = ""

                edad_val = datuar_data.get("edad")
                edad = int(edad_val) if edad_val and str(edad_val).isdigit() else None

                domicilio = cuit_data.get("direccion") or ""
                localidad = cuit_data.get("localidad") or datuar_data.get("ciudad") or ""
                ciudad = datuar_data.get("ciudad") or ""
                municipio = datuar_data.get("municipio") or ""
                provincia = cuit_data.get("provincia") or datuar_data.get("provincia") or ""
                condicion_afip = f"IVA: {cuit_data.get('iva', '')} | Ganancias: {cuit_data.get('ganancias', '')}".strip(" |") if cuit_data else ""
                tipo_persona = cuit_data.get("tipo_persona") or ""
                empleador = cuit_data.get("empleador") or ""
                actividades = json.dumps(cuit_data.get("actividades", []), ensure_ascii=False) if cuit_data.get("actividades") else ""
                constancia_afip_url = cuit_data.get("constancia_inscripcion_afip") or ""

                peor_sit = bcra_data.get("peor_situacion", 0)
                deuda_total_miles = bcra_data.get("deuda_total_miles", 0.0)
                deuda_macro_miles = bcra_data.get("deuda_macro_miles", 0.0)
                deuda_macro_sit = bcra_data.get("deuda_macro_situacion")

                # Payload completo consolidado sin omitir ningún campo de cada scraper
                datos_completos = {
                    "cuitonline": cuit_data,
                    "datuar": datuar_data,
                    "bcra": bcra_data,
                    "iris": {
                        "total_lineas": len(lineas_descubiertas),
                        "total_operaciones": len(operaciones_iris),
                        "lineas_descubiertas": lineas_descubiertas
                    }
                }
                datos_json_str = json.dumps(datos_completos, ensure_ascii=False)

                if error_msg and "horario comercial" in error_msg:
                    estado_dest = "pendiente"
                elif error_msg:
                    estado_dest = "error"
                else:
                    estado_dest = "completado"

                # Asegurar existencia previa para satisfacer Foreign Keys
                cursor.execute("""
                    INSERT OR IGNORE INTO personas (dni, nombre_excel, total_operaciones_excel)
                    VALUES (?, ?, 1)
                """, (dni, nombre_oficial or "Consulta Directa"))

                cursor.execute("""
                    UPDATE personas
                    SET cuit = COALESCE(NULLIF(?, ''), cuit),
                        nombre_oficial = COALESCE(NULLIF(?, ''), nombre_oficial),
                        genero = COALESCE(NULLIF(?, ''), genero),
                        edad = COALESCE(?, edad),
                        domicilio_fiscal = COALESCE(NULLIF(?, ''), domicilio_fiscal),
                        localidad = COALESCE(NULLIF(?, ''), localidad),
                        ciudad = COALESCE(NULLIF(?, ''), ciudad),
                        municipio = COALESCE(NULLIF(?, ''), municipio),
                        provincia = COALESCE(NULLIF(?, ''), provincia),
                        condicion_afip = COALESCE(NULLIF(?, ''), condicion_afip),
                        actividades_afip = COALESCE(NULLIF(?, ''), actividades_afip),
                        tipo_persona = COALESCE(NULLIF(?, ''), tipo_persona),
                        empleador = COALESCE(NULLIF(?, ''), empleador),
                        constancia_afip_url = COALESCE(NULLIF(?, ''), constancia_afip_url),
                        peor_situacion_bcra = COALESCE(?, peor_situacion_bcra),
                        deuda_macro_miles = COALESCE(?, deuda_macro_miles),
                        deuda_macro_situacion = COALESCE(?, deuda_macro_situacion),
                        deuda_total_bcra_miles = COALESCE(?, deuda_total_bcra_miles),
                        total_lineas_iris = ?,
                        lineas_activas_resumen = ?,
                        datos_json = ?,
                        estado_proceso = ?,
                        error_msg = ?,
                        fecha_actualizacion = ?
                    WHERE dni = ?
                """, (
                    cuit, nombre_oficial, genero, edad, domicilio, localidad, ciudad, municipio, provincia,
                    condicion_afip, actividades, tipo_persona, empleador, constancia_afip_url,
                    peor_sit, deuda_macro_miles, deuda_macro_sit, deuda_total_miles,
                    len(lineas_descubiertas), lineas_activas_resumen, datos_json_str,
                    estado_dest, error_msg, now_str, dni
                ))

                # Limpieza previa de tablas hijas para garantizar idempotencia en reintentos
                cursor.execute("DELETE FROM bcra_entidades WHERE dni = ?", (dni,))
                cursor.execute("DELETE FROM operaciones_iris WHERE dni = ?", (dni,))
                cursor.execute("DELETE FROM lineas_descubiertas WHERE dni = ?", (dni,))
                cursor.execute("DELETE FROM telcos_scraping WHERE dni = ?", (dni,))

                # 2. Insertar entidades BCRA
                for ent in bcra_data.get("entidades", []):
                    cursor.execute("""
                        INSERT INTO bcra_entidades (dni, cuit, entidad, situacion, monto_miles, periodo)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        dni, cuit, ent.get("entidad", ""), ent.get("situacion", 1),
                        ent.get("monto_miles", 0.0), ent.get("periodo", "")
                    ))

                # 3. Insertar operaciones IRIS
                for op in operaciones_iris:
                    det = op.get("detalle", {})
                    cursor.execute("""
                        INSERT INTO operaciones_iris (
                            dni, nro_tramite_abd, id_tramite_spn, tipo_operacion,
                            operador_receptor, fecha_operacion, estado, producto, tecnologia
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        dni,
                        det.get("nro_tramite_abd") or op.get("nro_tramite"),
                        det.get("id_tramite_spn"),
                        op.get("operacion"),
                        det.get("operador_receptor"),
                        det.get("fecha_operacion") or op.get("fecha_alta"),
                        det.get("estado") or op.get("estado"),
                        det.get("producto") or op.get("producto"),
                        det.get("tecnologia")
                    ))

                # 4. Insertar líneas descubiertas
                for linea_str in lineas_descubiertas:
                    cursor.execute("""
                        INSERT OR IGNORE INTO lineas_descubiertas (dni, ani, origen_extraccion)
                        VALUES (?, ?, 'iris_port_out')
                    """, (dni, linea_str))

                # 5. Insertar resultados de Telcos
                for t in telcos_resultados:
                    cursor.execute("""
                        INSERT INTO telcos_scraping (
                            dni, ani, operador_detectado, tiene_deuda, deuda_total, detalles_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        dni, t["ani"], t["operador"], 1 if t["tiene_deuda"] else 0,
                        t["deuda_total"], t["detalles_json"]
                    ))

                conn.commit()
                logger.info(f"[{dni}] Persistencia SQLite completada con éxito.")

            except Exception as e:
                conn.rollback()
                logger.error(f"[{dni}] Error en persistencia SQLite: {e}")
                cursor.execute("UPDATE personas SET estado_proceso = 'error', error_msg = ? WHERE dni = ?", (str(e), dni))
                conn.commit()
            finally:
                conn.close()

    def procesar_cola(self, limite: Optional[int] = None):
        """Procesa registros pendientes en SQLite distribuyendo en N workers concurrentes."""
        conn = self.get_db()
        cursor = conn.cursor()

        query = """
            SELECT dni, nombre_excel 
            FROM personas 
            WHERE estado_proceso = 'pendiente'
            ORDER BY total_operaciones_excel DESC, dni ASC
        """
        if limite:
            query += f" LIMIT {limite}"

        cursor.execute(query)
        filas = cursor.fetchall()
        conn.close()

        total = len(filas)
        if total == 0:
            print("🎉 No hay personas pendientes por procesar en SQLite.")
            return

        print(f"\n🚀 Iniciando procesamiento concurrente de {total} personas pendientes en SQLite...")
        print(f"⚡ Configuración: {self.iris_workers} sesiones de IRIS (directo/sin Tor) | {self.telcos_workers} workers de Telcos (Tor Stream Isolation)")

        cola_trabajo: queue.Queue = queue.Queue()
        for idx, row in enumerate(filas, 1):
            cola_trabajo.put((idx, row["dni"], row["nombre_excel"]))

        contador_procesados = 0
        contador_lock = threading.Lock()

        def vaciar_cola():
            while not cola_trabajo.empty():
                try:
                    cola_trabajo.get_nowait()
                    cola_trabajo.task_done()
                except queue.Empty:
                    break

        def worker_loop(w_id: int):
            nonlocal contador_procesados
            if self.stop_event.is_set():
                return
            time.sleep((w_id - 1) * 0.4)  # Desfase de inicio para evitar pico de autenticación
            worker = MacroEnricherWorker(worker_id=w_id, orchestrator=self)
            try:
                worker.iniciar()
                while not cola_trabajo.empty() and not self.stop_event.is_set():
                    try:
                        idx, dni, nombre_excel = cola_trabajo.get_nowait()
                    except queue.Empty:
                        break

                    if self.stop_event.is_set():
                        cola_trabajo.task_done()
                        break

                    with contador_lock:
                        contador_procesados += 1
                        num_act = contador_procesados

                    print(f"\n{'='*70}")
                    print(f"🔄 [W{w_id}] [{num_act}/{total}] Procesando DNI: {dni} | {nombre_excel}")
                    print(f"{'='*70}")

                    try:
                        worker.procesar_persona(dni, nombre_excel)
                    except Exception as e:
                        logger.error(f"[W{w_id}] Error procesando {dni}: {e}")
                    finally:
                        cola_trabajo.task_done()
            finally:
                worker.cerrar()

        def sigint_handler(sig, frame):
            print("\n\n🛑 [CTRL+C] Interrupción de usuario detectada. Deteniendo de forma inmediata...")
            self.stop_event.set()
            vaciar_cola()

        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, sigint_handler)
        if hasattr(signal, "SIGBREAK"):  # Ctrl+Break en Windows
            original_sigbreak = signal.getsignal(signal.SIGBREAK)
            signal.signal(signal.SIGBREAK, sigint_handler)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.iris_workers)
        futures = [executor.submit(worker_loop, i) for i in range(1, self.iris_workers + 1)]

        try:
            # En Windows, un while con sleep(0.2) en el hilo principal permite que el intérprete
            # procese el SIGINT inmediatamente sin quedar bloqueado en llamadas de C
            while any(not f.done() for f in futures):
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n\n🛑 [CTRL+C] KeyboardInterrupt capturado en hilo principal.")
            self.stop_event.set()
            vaciar_cola()
        finally:
            signal.signal(signal.SIGINT, original_sigint)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, original_sigbreak)

            if self.stop_event.is_set():
                print("⏳ Cancelando tareas pendientes en cola y cerrando workers...")
                vaciar_cola()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        if self.stop_event.is_set():
            print("\n" + "="*70)
            print("🛑 Proceso detenido por el usuario (Ctrl+C). La base de datos SQLite permanece íntegra.")
            print("="*70)
        else:
            print("\n" + "="*70)
            print("🎉 Lote de procesamiento finalizado.")
            print("="*70)

    def exportar_excel(self, output_path: Optional[Path] = None):
        """Exporta la base enriquecida a un archivo Excel."""
        out = output_path or (PROJECT_ROOT / "base_macro_enriquecida.xlsx")
        print(f"📊 Exportando datos enriquecidos a {out}...")

        conn = self.get_db()
        df = pd.read_sql_query("""
            SELECT 
                p.dni AS "DNI",
                p.nombre_excel AS "Nombre Excel",
                p.nombre_oficial AS "Nombre Oficial",
                p.cuit AS "CUIT/CUIL",
                p.genero AS "Género",
                p.edad AS "Edad",
                p.domicilio_fiscal AS "Domicilio Fiscal",
                p.localidad AS "Localidad",
                p.ciudad AS "Ciudad (Datuar)",
                p.municipio AS "Municipio (Datuar)",
                p.provincia AS "Provincia",
                p.tipo_persona AS "Tipo Persona AFIP",
                p.empleador AS "Empleador AFIP",
                p.condicion_afip AS "Condición AFIP",
                p.actividades_afip AS "Actividades AFIP",
                p.constancia_afip_url AS "Constancia AFIP URL",
                p.peor_situacion_bcra AS "Peor Sit. BCRA",
                p.deuda_macro_miles AS "Deuda Macro ($ Miles)",
                p.deuda_macro_situacion AS "Sit. Banco Macro",
                p.deuda_total_bcra_miles AS "Deuda Total Finan. ($ Miles)",
                p.total_lineas_iris AS "Líneas Halladas IRIS",
                p.lineas_activas_resumen AS "Auditoría Telcos Activas",
                p.estado_proceso AS "Estado",
                p.fecha_actualizacion AS "Fecha Procesado"
            FROM personas p
            ORDER BY p.deuda_macro_miles DESC, p.total_operaciones_excel DESC
        """, conn)
        conn.close()

        df.to_excel(out, index=False)
        print(f"✅ Excel generado con éxito: {out} ({len(df)} registros)")


def main():
    parser = argparse.ArgumentParser(description="Orquestador Enriquecimiento Banco Macro")
    parser.add_argument("--limit", type=int, default=None, help="Cantidad máxima de DNIs a procesar")
    parser.add_argument("--dni", type=str, default=None, help="Procesar un DNI puntual para prueba")
    parser.add_argument("--skip-iris", action="store_true", help="Omitir consulta a IRIS")
    parser.add_argument("--skip-telcos", action="store_true", help="Omitir consulta a Telcos")
    parser.add_argument("--forzar-horario", action="store_true", help="Forzar ejecución de IRIS fuera de horario comercial")
    parser.add_argument("--iris-workers", type=int, default=5, help="Cantidad de sesiones HTTP concurrentes en IRIS (sin Tor) (default: 5)")
    parser.add_argument("--telcos-workers", type=int, default=15, help="Cantidad de workers/instancias de Telcos en Tor Stream Isolation (default: 15)")
    parser.add_argument("--export-excel", action="store_true", help="Exportar datos a Excel y salir")

    args = parser.parse_args()

    orchestrator = BancoMacroOrchestrator(
        skip_iris=args.skip_iris,
        skip_telcos=args.skip_telcos,
        forzar_horario_iris=args.forzar_horario,
        iris_workers=args.iris_workers,
        telcos_workers=args.telcos_workers
    )

    try:
        if args.export_excel:
            orchestrator.exportar_excel()
            return

        if args.dni:
            print(f"🎯 Procesando DNI específico: {args.dni}")
            orchestrator.procesar_persona(args.dni, "Consulta Manual")
            return

        orchestrator.procesar_cola(limite=args.limit)

    finally:
        orchestrator.cerrar()


if __name__ == "__main__":
    main()
