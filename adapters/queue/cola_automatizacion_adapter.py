# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Repositorio de Cola General (cola_automatizacion)
Arquitectura Hexagonal - Puerto de Salida IColaRepositorioPort

Permite consumir tareas polimórficas de la tabla `cola_automatizacion` en MySQL VPS,
ejecutar el scraping por línea telefónica y persistir resultados enriquecidos
en la columna `resultado` (JSON), manteniendo 100% de compatibilidad con los
sistemas downstream y dashboards legados.
"""
import os
import re
import json
import random
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

import config
from core.ports.queue_port import IColaRepositorioPort
from core.domain.entities import Linea, RegistroCola, Prioridad, EstadoRegistro, ReglaPipeline

logger = logging.getLogger("ColaAutomatizacionAdapter")


class ColaAutomatizacionAdapter(IColaRepositorioPort):
    """Adaptador de persistencia para la tabla general `cola_automatizacion`."""

    def __init__(
        self,
        pool_size: int = 1,
        pool_name: Optional[str] = None,
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None
    ):
        self.auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "iris_scraper")
        self.pc_id = pc_id or getattr(config, "WORKER_PC_ID", "PC-NODE")
        random_suffix = random.randint(1000, 9999)
        self.instance_id = f"{self.pc_id}_{os.getpid()}_{random_suffix}"
        self.table = "cola_automatizacion"

        p_name = pool_name or f"pool_cola_auto_{os.getpid()}_{random_suffix}"

        user = getattr(config, "COLA_AUTO_DBUSER", "automatizaciones")
        password = getattr(config, "COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q")

        self.pool = MySQLConnectionPool(
            pool_name=p_name,
            pool_size=pool_size,
            host=config.VPS_DBHOST,
            port=config.VPS_DBPORT,
            user=user,
            password=password,
            database=config.VPS_DBNAME,
            use_pure=getattr(config, "VPS_DB_USE_PURE", True),
            autocommit=False,
            connection_timeout=20
        )

    def _get_connection(self, max_retries: int = 4, retry_delay: float = 2.0):
        for attempt in range(1, max_retries + 1):
            conn = None
            try:
                conn = self.pool.get_connection()
                if hasattr(conn, "unread_result") and conn.unread_result:
                    try:
                        conn.consume_results()
                    except Exception:
                        pass
                conn.ping(reconnect=True, attempts=3, delay=1)
                return conn
            except Exception as e:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                logger.warning(f"Reintento {attempt}/{max_retries} conexión cola_automatizacion: {e}")
                if attempt == max_retries:
                    raise
                import time
                time.sleep(retry_delay * (1.5 ** (attempt - 1)))

    def reservar_lote(
        self,
        batch_size: int = 20,
        prioridad: Optional[int] = None,
        scraper_nombre: str = "iris",
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        """
        Reclama atómicamente un lote de tareas desde `cola_automatizacion`
        utilizando SELECT ... FOR UPDATE SKIP LOCKED.
        """
        conn = None
        cursor = None
        registros: List[RegistroCola] = []

        query_find = f"""
            SELECT id, numero_de_linea, dni, payload, prioridad
            FROM {self.table}
            WHERE estado = 'pendiente' AND auto_id = %s
              AND (target_pc IS NULL OR target_pc = '' OR FIND_IN_SET(%s, target_pc) > 0 OR target_pc = 'PC-00')
            ORDER BY (target_pc IS NOT NULL AND target_pc != '') DESC,
                     prioridad ASC,
                     dias_desde_ultimo_cambio ASC,
                     id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """

        try:
            conn = self._get_connection()
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True, buffered=True)

            cursor.execute(query_find, (self.auto_id, self.pc_id, batch_size))
            filas = cursor.fetchall()

            if not filas:
                conn.rollback()
                return []

            ids_reclamados = [f["id"] for f in filas]

            # Actualizar estado a 'en_proceso' de forma atómica dentro de la transacción
            placeholders = ", ".join(["%s"] * len(ids_reclamados))
            query_update = f"""
                UPDATE {self.table}
                SET estado = 'en_proceso',
                    instance_id = %s,
                    fecha_inicio = NOW()
                WHERE id IN ({placeholders})
            """
            cursor.execute(query_update, [self.instance_id] + ids_reclamados)
            conn.commit()

            # Mapear las filas reclamadas al modelo de dominio RegistroCola
            for f in filas:
                payload_raw = f.get("payload")
                payload_dict = {}
                if isinstance(payload_raw, dict):
                    payload_dict = payload_raw
                elif isinstance(payload_raw, str) and payload_raw.strip():
                    try:
                        payload_dict = json.loads(payload_raw)
                    except Exception:
                        payload_dict = {}

                # Extraer número de línea: prioridad a columna directa, luego payload
                num_linea = str(f.get("numero_de_linea") or "").strip()
                if not num_linea or not num_linea.isdigit():
                    num_linea = str(
                        payload_dict.get("numero_de_linea")
                        or payload_dict.get("linea")
                        or payload_dict.get("ani")
                        or ""
                    ).strip()

                num_linea_limpio = "".join(filter(str.isdigit, num_linea))

                # Extraer DNI de referencia si existe
                dni_val = str(f.get("dni") or payload_dict.get("dni") or "").strip()
                dni_limpio = "".join(filter(str.isdigit, dni_val)) if dni_val else None

                prio_int = f.get("prioridad", 2)
                prio_enum = Prioridad.P2_SUR_PATAGONIA
                if prio_int == 1:
                    prio_enum = Prioridad.P1_AMBA_MENDOZA
                elif prio_int == 3:
                    prio_enum = Prioridad.P3_RESTO

                linea_entidad = Linea(ani=num_linea_limpio, dni=dni_limpio)

                reg = RegistroCola(
                    id=f["id"],
                    linea=linea_entidad,
                    prioridad=prio_enum,
                    prioridad_nombre=f"P{prio_int}",
                    estado=EstadoRegistro.PROCESANDO,
                    scraper_actual=scraper_nombre,
                    fuente=f.get("auto_id"),
                    datos_existentes=payload_dict,
                    tipo_cola="cola_automatizacion"
                )
                registros.append(reg)

            logger.info(f"Reclamadas {len(registros)} tareas de {self.table} para auto_id='{self.auto_id}'")

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Error al reservar lote en {self.table}: {e}")
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return registros

    @staticmethod
    def _formatear_operacion_iris(op: Dict[str, Any]) -> Dict[str, Any]:
        """Formatea un trámite individual de IRIS incorporando exhaustivamente todos los campos de la grilla y del detalle."""
        det = op.get("detalle", {}) or {}

        # Base de campos tomados de la grilla
        registro: Dict[str, Any] = {
            "nro_tramite": op.get("nro_tramite", ""),
            "formulario": op.get("formulario", ""),
            "fecha_alta": op.get("fecha_alta", ""),
            "estado": op.get("estado", ""),
            "canal": op.get("canal", ""),
            "producto": op.get("producto", ""),
        }

        # Titular compuesto y nombres individuales si están presentes
        nom = det.get("nombre", "").strip()
        ape = det.get("apellido", "").strip()
        titular_comp = f"{nom} {ape}".strip()
        if titular_comp:
            registro["titular"] = titular_comp
            if nom:
                registro["nombre"] = nom
            if ape:
                registro["apellido"] = ape

        # Incorporar todos los campos del detalle exhaustivo sin descartar ninguno
        for k, v in det.items():
            if v is not None and v != "":
                registro[k] = v
            elif k not in registro:
                registro[k] = v

        if "ultima_modificacion" not in registro:
            registro["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return registro

    @classmethod
    def _agrupar_operaciones_iris(cls, registros: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Agrupa las operaciones por categoría normalizada (altas, port_out, cambios, port_in, etc.)."""
        grupos: Dict[str, List[Dict[str, Any]]] = {}
        for op in registros:
            nombre_op = str(op.get("operacion", "")).strip()
            clave = re.sub(r'[\s\-]+', '_', nombre_op.lower()).strip('_')
            if not clave:
                clave = "otros"
            if clave == "alta":
                clave = "altas"
            elif clave == "cambio":
                clave = "cambios"
            elif clave in ("port_out", "portout", "solicitud_portout"):
                clave = "port_out"
            elif clave in ("port_in", "portin", "solicitud_portin", "poin"):
                clave = "port_in"

            if clave not in grupos:
                grupos[clave] = []
            grupos[clave].append(cls._formatear_operacion_iris(op))

        # Estructurar en sub-objetos anidados:
        # Si es un solo trámite: se mantiene la clave directa (ej. iris.port_in.port_in)
        # Si son varios trámites del mismo tipo: se numeran (ej. iris.port_out.port_out_1, port_out_2)
        resultado: Dict[str, Any] = {}
        for clave, ops in grupos.items():
            if len(ops) == 1:
                resultado[clave] = {
                    clave: ops[0]
                }
            else:
                resultado[clave] = {
                    f"{clave}_{i+1}": op for i, op in enumerate(ops)
                }

        return resultado

    def _formatear_resultado(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Formatea el resultado para `cola_automatizacion.resultado`:
        - status: "completado" | "no_encontrado"
        - timestamp: ISO 8601
        - enacom: Bloque oficial ENACOM
        - iris: Operaciones agrupadas directamente por categoría (altas, port_out, cambios, port_in, etc.)
        """
        datos_totales = item.get("datos")
        if not isinstance(datos_totales, dict):
            if isinstance(datos_totales, str) and datos_totales.strip():
                try:
                    datos_totales = json.loads(datos_totales)
                except Exception:
                    datos_totales = {}
            else:
                datos_totales = {}

        status_raw = str(item.get("status", "")).lower()
        es_coincidencia = status_raw in ("coincidencia", "completado", "success")

        datos_iris = datos_totales.get("iris", {}) if isinstance(datos_totales.get("iris"), dict) else {}
        detalles = datos_iris.get("detalles", {}) if isinstance(datos_iris, dict) else {}
        raw = datos_iris.get("raw", {}) if isinstance(datos_iris, dict) else {}

        ani_actual = str(item.get("ani", "")).strip()

        # 1. Base limpia sin marcas de versión ni metadatos en la raíz
        resultado_final: Dict[str, Any] = {}

        # 2. Bloque ENACOM: recuperar de datos acumulados o consultar EnacomBlockAdapter determinísticamente
        bloque_enacom = datos_totales.get("enacom")
        if not bloque_enacom and ani_actual:
            try:
                from adapters.enacom.enacom_adapter import EnacomBlockAdapter
                enacom_adapter = EnacomBlockAdapter()
                bloque_enacom = enacom_adapter.consultar_bloque_dict(ani_actual)
            except Exception as e_enacom:
                logger.debug(f"No se pudo consultar bloque ENACOM para {ani_actual}: {e_enacom}")

        if bloque_enacom:
            if isinstance(bloque_enacom, dict) and "ultima_modificacion" not in bloque_enacom:
                bloque_enacom["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            resultado_final["enacom"] = bloque_enacom

        # 3. Registros históricos de operaciones de IRIS agrupados por tipo con ultima_modificacion
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        registros_hist = []
        if isinstance(raw, dict) and "registros" in raw:
            registros_hist = raw["registros"]
        elif isinstance(detalles, dict) and "registros" in detalles:
            registros_hist = detalles["registros"]

        if es_coincidencia:
            if registros_hist:
                iris_dict = self._agrupar_operaciones_iris(registros_hist)
                iris_dict["ultima_modificacion"] = ahora_str
                resultado_final["iris"] = iris_dict
            elif datos_iris:
                iris_dict = dict(datos_iris)
                iris_dict["ultima_modificacion"] = ahora_str
                resultado_final["iris"] = iris_dict
        elif datos_iris or registros_hist:
            resultado_final["linea"] = ani_actual
            resultado_final["iris"] = {
                "message": item.get("descripcion", "Sin registros en IRIS"),
                "ultima_modificacion": ahora_str
            }

        # 4. Bloque DATUAR
        datos_datuar = datos_totales.get("datuar")
        if isinstance(datos_datuar, dict) and datos_datuar:
            det_dat = datos_datuar.get("detalles", {}) if isinstance(datos_datuar.get("detalles"), dict) else {}
            datuar_clean = {
                "nombre_completo": det_dat.get("nombre_completo") or datos_datuar.get("nombre_completo", ""),
                "cuil": det_dat.get("cuil") or datos_datuar.get("cuil", ""),
                "dni": det_dat.get("dni") or datos_datuar.get("dni", ""),
                "edad": det_dat.get("edad") if det_dat.get("edad") is not None else datos_datuar.get("edad"),
                "genero": det_dat.get("genero") or datos_datuar.get("genero", ""),
                "provincia": det_dat.get("provincia") or datos_datuar.get("provincia", ""),
                "ciudad": det_dat.get("ciudad") or datos_datuar.get("ciudad", ""),
                "municipio": det_dat.get("municipio") or datos_datuar.get("municipio", ""),
                "status": datos_datuar.get("status", "coincidencia"),
                "ultima_modificacion": datos_datuar.get("ultima_modificacion") or ahora_str
            }
            resultado_final["datuar"] = {k: v for k, v in datuar_clean.items() if v is not None and v != ""}

        # 5. Bloque CUITONLINE
        datos_cuit = datos_totales.get("cuitonline")
        if isinstance(datos_cuit, dict) and datos_cuit:
            det_cuit = datos_cuit.get("detalles", {}) if isinstance(datos_cuit.get("detalles"), dict) else {}
            cuit_clean_block = {
                "cuit": det_cuit.get("cuit") or datos_cuit.get("cuit", ""),
                "cuit_limpio": det_cuit.get("cuit_limpio") or datos_cuit.get("cuit_limpio", ""),
                "denominacion": det_cuit.get("denominacion") or datos_cuit.get("denominacion", ""),
                "condicion_afip": det_cuit.get("condicion_afip") or datos_cuit.get("condicion_afip", ""),
                "tipo_persona": det_cuit.get("tipo_persona") or datos_cuit.get("tipo_persona", ""),
                "genero": det_cuit.get("genero") or datos_cuit.get("genero", ""),
                "direccion": det_cuit.get("direccion") or datos_cuit.get("direccion", ""),
                "localidad": det_cuit.get("localidad") or datos_cuit.get("localidad", ""),
                "provincia": det_cuit.get("provincia") or datos_cuit.get("provincia", ""),
                "actividades": det_cuit.get("actividades") or datos_cuit.get("actividades", []),
                "impuestos_activos": det_cuit.get("impuestos_activos") or datos_cuit.get("impuestos_activos", []),
                "regimenes_activos": det_cuit.get("regimenes_activos") or datos_cuit.get("regimenes_activos", []),
                "iva": det_cuit.get("iva") or datos_cuit.get("iva", ""),
                "ganancias": det_cuit.get("ganancias") or datos_cuit.get("ganancias", ""),
                "empleador": det_cuit.get("empleador") or datos_cuit.get("empleador", ""),
                "status": datos_cuit.get("status", "coincidencia"),
                "ultima_modificacion": datos_cuit.get("ultima_modificacion") or ahora_str
            }
            resultado_final["cuitonline"] = {k: v for k, v in cuit_clean_block.items() if v is not None and v != "" and v != []}

        # 6. Bloque BCRA
        datos_bcra = datos_totales.get("bcra")
        if isinstance(datos_bcra, dict) and datos_bcra:
            det_bcra = datos_bcra.get("detalles", {}) if isinstance(datos_bcra.get("detalles"), dict) else {}
            bcra_clean_block = {
                "cuit": det_bcra.get("cuit") or datos_bcra.get("cuit", ""),
                "denominacion": det_bcra.get("denominacion") or datos_bcra.get("denominacion", ""),
                "periodo": det_bcra.get("periodo") or datos_bcra.get("periodo", ""),
                "peor_situacion": det_bcra.get("peor_situacion") if det_bcra.get("peor_situacion") is not None else datos_bcra.get("peor_situacion", 0),
                "cantidad_entidades": det_bcra.get("cantidad_entidades") or datos_bcra.get("cantidad_entidades") or len(det_bcra.get("entidades") or datos_bcra.get("entidades") or []),
                "operaciones_en_cartera": det_bcra.get("operaciones_en_cartera") or datos_bcra.get("operaciones_en_cartera") or len(det_bcra.get("entidades") or datos_bcra.get("entidades") or []),
                "deuda_total_pesos": det_bcra.get("deuda_total_pesos") if det_bcra.get("deuda_total_pesos") is not None else datos_bcra.get("deuda_total_pesos", 0.0),
                "deuda_total_miles": det_bcra.get("deuda_total_miles") if det_bcra.get("deuda_total_miles") is not None else datos_bcra.get("deuda_total_miles", 0.0),
                "sin_deuda": det_bcra.get("sin_deuda") if det_bcra.get("sin_deuda") is not None else datos_bcra.get("sin_deuda", True),
                "entidades": det_bcra.get("entidades") or datos_bcra.get("entidades", []),
                "status": datos_bcra.get("status", "coincidencia"),
                "ultima_modificacion": datos_bcra.get("ultima_modificacion") or ahora_str
            }
            if det_bcra.get("deuda_macro_pesos"):
                bcra_clean_block["deuda_macro_pesos"] = det_bcra["deuda_macro_pesos"]
            if det_bcra.get("deuda_macro_miles"):
                bcra_clean_block["deuda_macro_miles"] = det_bcra["deuda_macro_miles"]
            if det_bcra.get("deuda_macro_situacion"):
                bcra_clean_block["deuda_macro_situacion"] = det_bcra["deuda_macro_situacion"]

            resultado_final["bcra"] = {k: v for k, v in bcra_clean_block.items() if v is not None and v != ""}

        # 7. Cualquier otra fuente acumulada (claro, personal, movistar, etc.)
        for k, v in datos_totales.items():
            if k not in ("enacom", "iris", "iris_v2", "datuar", "cuitonline", "bcra"):
                resultado_final[k] = v

        return resultado_final


    def persistir_resultados(self, resultados: List[Dict[str, Any]]) -> bool:
        """
        Actualiza los registros procesados en `cola_automatizacion`:
        - estado = 'completado' (o 'fallido' si ocurrió una excepción de red/sistema)
        - resultado = JSON con bloques directos `enacom` e `iris` si completado (NULL si fallido)
        - error_msg = Detalle estructurado [TIPO:CODIGO] del error si fallido (NULL si completado)
        - fecha_fin = CURRENT_TIMESTAMP
        - paso_por_iris = 1
        - numero_de_linea = asegura ANI si estaba vacío
        - dni = propaga DNI extraído de IRIS si estaba vacío
        - scrapers_intentados = acumulativo con marca 'iris'
        Aplica cursor.executemany(...) en una única transacción atómica para minimizar eventos en binlog.
        """
        if not resultados:
            return True

        conn = None
        cursor = None

        query_update = f"""
            UPDATE {self.table}
            SET estado = %s,
                resultado = %s,
                error_msg = %s,
                fecha_fin = CURRENT_TIMESTAMP,
                numero_de_linea = CASE 
                    WHEN (numero_de_linea IS NULL OR numero_de_linea = '') AND %s IS NOT NULL AND %s != '' THEN %s 
                    ELSE numero_de_linea 
                END,
                dni = CASE 
                    WHEN (dni IS NULL OR dni = '') AND %s IS NOT NULL AND %s != '' THEN %s 
                    ELSE dni 
                END,
                scrapers_intentados = CASE 
                    WHEN scrapers_intentados IS NULL OR scrapers_intentados = '' THEN '{self.auto_id}'
                    WHEN FIND_IN_SET('{self.auto_id}', scrapers_intentados) > 0 THEN scrapers_intentados
                    ELSE CONCAT(scrapers_intentados, ',{self.auto_id}')
                END
            WHERE id = %s
        """

        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)

            valores_lote = []
            delta_enriquecidos = 0

            for item in resultados:
                tarea_id = item["id"]
                ani = str(item.get("ani") or "").strip()
                status_raw = str(item.get("status", "")).lower()

                if status_raw in ("error", "failed", "fallido"):
                    estado_final = "fallido"
                    err_code = item.get("error_codigo")
                    err_type = item.get("error_tipo")
                    desc = item.get("descripcion") or "Error en procesamiento"
                    if err_type and err_code and not desc.startswith("["):
                        error_msg = f"[{err_type}:{err_code}] {desc}"
                    elif err_type and not desc.startswith("["):
                        error_msg = f"[{err_type}] {desc}"
                    else:
                        error_msg = desc
                    res_json_str = None
                    dni_extraido = None
                else:
                    estado_final = "completado"
                    error_msg = None
                    datos_completos = self._formatear_resultado(item)
                    res_json_str = json.dumps(datos_completos, ensure_ascii=False)

                    # Extraer DNI de cualquier etapa recorrida (Datuar, CuitOnline, BCRA, IRIS)
                    dni_extraido = ReglaPipeline.extraer_dni(datos_completos)
                    if not dni_extraido and isinstance(datos_completos, dict):
                        iris_dict = datos_completos.get("iris", {})
                        if isinstance(iris_dict, dict):
                            for k_op, op_list in iris_dict.items():
                                if isinstance(op_list, list):
                                    for op in op_list:
                                        if isinstance(op, dict) and op.get("nro_documento"):
                                            dni_extraido = str(op["nro_documento"]).strip()
                                            break
                                    if dni_extraido:
                                        break
                            if not dni_extraido and isinstance(iris_dict.get("titular"), dict):
                                dni_extraido = str(iris_dict["titular"].get("nro_documento") or "").strip() or None
                        elif datos_completos.get("dni"):
                            dni_extraido = str(datos_completos.get("dni")).strip()


                    iris_status = datos_completos.get("iris", {}).get("status") if isinstance(datos_completos, dict) and isinstance(datos_completos.get("iris"), dict) else ""
                    if iris_status == "coincidencia" or status_raw == "coincidencia":
                        delta_enriquecidos += 1

                valores_lote.append((
                    estado_final,
                    res_json_str,
                    error_msg,
                    ani, ani, ani,
                    dni_extraido, dni_extraido, dni_extraido,
                    tarea_id
                ))

            cursor.executemany(query_update, valores_lote)
            conn.commit()

            logger.info(f"Persistidos masivamente {len(resultados)} resultados en {self.table} (Enriquecidos: {delta_enriquecidos})")
            return True

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Error al persistir resultados masivos en {self.table}: {e}")
            return False
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def enviar_heartbeat(self) -> bool:
        """Escribe el pulso de vida de esta PC en la tabla worker_heartbeats de forma espaciada (1/min)."""
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)
            query = """
                INSERT INTO worker_heartbeats (pc_id, auto_id, last_seen, status)
                VALUES (%s, %s, NOW(), 'online')
                ON DUPLICATE KEY UPDATE auto_id = VALUES(auto_id), last_seen = NOW(), status = 'online'
            """
            cursor.execute(query, (self.pc_id, self.auto_id))
            conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Aviso al enviar heartbeat: {e}")
            return False
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _send_heartbeat(self, cursor):
        """Compatibilidad interna: redirige a enviar_heartbeat."""
        return self.enviar_heartbeat()

    def guardar_session_stats(self, delta_p: int, delta_e: int, delta_f: int) -> bool:
        """
        Actualiza las estadísticas diarias acumulativas en stats_historial periódicamente.
        Elimina el SELECT COUNT(*) para proteger el Buffer Pool y la memoria RAM del VPS.
        """
        if delta_p == 0 and delta_e == 0 and delta_f == 0:
            return True
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)
            identificador = f"{self.pc_id}_{self.auto_id}"

            query = """
                INSERT INTO stats_historial (fecha, tipo, identificador, datos)
                VALUES (CURDATE(), 'worker_stats', %s, %s)
                ON DUPLICATE KEY UPDATE 
                    datos = JSON_SET(
                        datos,
                        '$.procesados', CAST(JSON_UNQUOTE(JSON_EXTRACT(datos, '$.procesados')) AS UNSIGNED) + %s,
                        '$.enriquecidos', CAST(JSON_UNQUOTE(JSON_EXTRACT(datos, '$.enriquecidos')) AS UNSIGNED) + %s,
                        '$.fallidos', CAST(JSON_UNQUOTE(JSON_EXTRACT(datos, '$.fallidos')) AS UNSIGNED) + %s
                    )
            """
            initial_stats = json.dumps({
                "procesados": delta_p,
                "enriquecidos": delta_e,
                "fallidos": delta_f
            })
            cursor.execute(query, (identificador, initial_stats, delta_p, delta_e, delta_f))
            conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Aviso al actualizar stats_historial periódicamente: {e}")
            return False
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _save_session_stats(self, cursor, delta_p: int, delta_e: int, delta_f: int):
        """Compatibilidad interna: redirige a guardar_session_stats."""
        return self.guardar_session_stats(delta_p, delta_e, delta_f)

    def revertir_a_pendiente(self, ids: List[int]) -> bool:
        """Devuelve una lista de IDs del estado 'en_proceso' al estado 'pendiente'."""
        if not ids:
            return True

        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)
            placeholders = ", ".join(["%s"] * len(ids))
            query = f"""
                UPDATE {self.table}
                SET estado = 'pendiente',
                    instance_id = NULL
                WHERE id IN ({placeholders}) AND estado = 'en_proceso'
            """
            cursor.execute(query, ids)
            conn.commit()
            logger.info(f"Revertidos {cursor.rowcount} registros a 'pendiente' en {self.table}")
            return True
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Error al revertir a pendiente en {self.table}: {e}")
            return False
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        """Watchdog: Rescata tareas bloqueadas en 'en_proceso' tras caídas imprevistas protegiendo con mutex distribuido."""
        conn = None
        cursor = None
        liberados = 0
        lock_adquirido = False
        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)

            # Mutex distribuido MySQL: GET_LOCK con timeout 0s (no-wait)
            # Solo 1 nodo entre todas las máquinas concurrentes ejecutará el barrido
            cursor.execute("SELECT GET_LOCK('watchdog_sweeper_cola_auto_mutex', 0)")
            row = cursor.fetchone()
            if not row or (isinstance(row, (list, tuple)) and row[0] != 1) or (isinstance(row, dict) and list(row.values())[0] != 1):
                logger.debug("Watchdog Sweeper cola_automatizacion: Mutex ya adquirido por otro nodo. Omitiendo ciclo.")
                return 0

            lock_adquirido = True

            query = f"""
                UPDATE {self.table}
                SET estado = 'pendiente',
                    instance_id = NULL
                WHERE estado = 'en_proceso'
                  AND auto_id = %s
                  AND fecha_inicio < NOW() - INTERVAL %s MINUTE
            """
            cursor.execute(query, (self.auto_id, minutos_inactividad))
            conn.commit()
            liberados = cursor.rowcount
            if liberados > 0:
                logger.info(f"Watchdog: Liberados {liberados} registros huérfanos en {self.table}")
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Error al liberar huérfanos en {self.table}: {e}")
        finally:
            if lock_adquirido and cursor:
                try:
                    cursor.execute("SELECT RELEASE_LOCK('watchdog_sweeper_cola_auto_mutex')")
                    cursor.fetchall()
                except Exception:
                    pass
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return liberados

    def obtener_estadisticas(self) -> Dict[str, int]:
        """Obtiene el conteo agrupado por estado para el auto_id actual."""
        conn = None
        cursor = None
        stats: Dict[str, int] = {}
        try:
            conn = self._get_connection()
            cursor = conn.cursor(buffered=True)
            query = f"""
                SELECT estado, COUNT(*)
                FROM {self.table}
                WHERE auto_id = %s
                GROUP BY estado
            """
            cursor.execute(query, (self.auto_id,))
            for estado, cnt in cursor.fetchall():
                stats[str(estado)] = cnt
        except Exception as e:
            logger.error(f"Error al obtener estadísticas de {self.table}: {e}")
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        return stats
