# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Sincronizador Remoto VPS MySQL
Arquitectura Hexagonal - Implementa ISyncRemoteRepoPort

Maneja de forma atómica y aislada:
- Descarga masiva (Pull) de hasta 10.000 tareas con SELECT ... FOR UPDATE SKIP LOCKED
  y transición atómica a 'en_proceso' / 'procesando' en MySQL VPS.
- Subida nocturna masiva (Push) en chunks de hasta 5.000 tareas por transacción
  utilizando executemany() y autocommit=False.
"""
import os
import time
import json
import random
import logging
import re
import threading
from typing import List, Dict, Any, Optional
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

import config
from core.ports.sync_port import ISyncRemoteRepoPort

logger = logging.getLogger("VPSSyncAdapter")


class VPSSyncAdapter(ISyncRemoteRepoPort):
    """Adaptador de operaciones en lote masivo contra MySQL 8 VPS."""

    def __init__(
        self,
        pool_size: int = 1,
        pool_name: Optional[str] = None
    ):
        self.pool_size = pool_size
        self.pool_name = pool_name or f"vps_sync_{os.getpid()}_{random.randint(1000, 9999)}"
        self._pools: Dict[str, MySQLConnectionPool] = {}
        self._pool_lock = threading.Lock()

    def _get_pool(self, tipo_cola: str) -> MySQLConnectionPool:
        tipo = (tipo_cola or "cola_automatizacion").lower().strip()
        with self._pool_lock:
            if tipo not in self._pools:
                p_name = f"{self.pool_name}_{tipo}"
                if tipo == "cola_automatizacion":
                    user = getattr(config, "COLA_AUTO_DBUSER", "automatizaciones")
                    password = getattr(config, "COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q")
                else:
                    user = getattr(config, "VPS_DBUSER", "ignacio_acuna")
                    password = getattr(config, "VPS_DBPASS", "goodTimes2026*")

                self._pools[tipo] = MySQLConnectionPool(
                    pool_name=p_name,
                    pool_size=self.pool_size,
                    host=config.VPS_DBHOST,
                    port=config.VPS_DBPORT,
                    user=user,
                    password=password,
                    database=config.VPS_DBNAME,
                    use_pure=getattr(config, "VPS_DB_USE_PURE", True),
                    autocommit=False,
                    connection_timeout=30
                )
            return self._pools[tipo]

    def _get_connection(self, tipo_cola: str = "cola_automatizacion", max_retries: int = 4, retry_delay: float = 2.0):
        pool = self._get_pool(tipo_cola)
        for attempt in range(1, max_retries + 1):
            conn = None
            try:
                conn = pool.get_connection()
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
                logger.warning(f"Reintento {attempt}/{max_retries} de conexión VPS Sync ({tipo_cola}): {e}")
                if attempt == max_retries:
                    raise
                time.sleep(retry_delay * (1.5 ** (attempt - 1)))

    def descargar_lote_vps(
        self,
        tipo_cola: str,
        limit: int = 10000,
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        scraper_actual: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Reclama atómicamente un bloque de tareas de VPS con FOR UPDATE SKIP LOCKED
        y las pasa a en_proceso/procesando dentro de la misma transacción.
        """
        tipo_cola_clean = (tipo_cola or "cola_automatizacion").lower().strip()
        conn = None
        cursor = None
        try:
            conn = self._get_connection(tipo_cola=tipo_cola_clean)
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True, buffered=True)

            if tipo_cola_clean == "cola_automatizacion":
                target_auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "telco_scraper")
                target_pc_id = pc_id or getattr(config, "WORKER_PC_ID", "PC-00")
                instance_id = f"{target_pc_id}_{os.getpid()}_{random.randint(1000, 9999)}"

                query_find = """
                    SELECT id, numero_de_linea, dni, auto_id, target_pc, payload, prioridad
                    FROM cola_automatizacion
                    WHERE estado = 'pendiente' AND auto_id = %s
                      AND (target_pc IS NULL OR target_pc = '' OR FIND_IN_SET(%s, target_pc) > 0 OR target_pc = 'PC-00')
                    ORDER BY (target_pc IS NOT NULL AND target_pc != '') DESC,
                             prioridad ASC,
                             dias_desde_ultimo_cambio ASC,
                             id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                """
                cursor.execute(query_find, (target_auto_id, target_pc_id, limit))
                filas = cursor.fetchall()

                if not filas:
                    conn.rollback()
                    return []

                ids = [f["id"] for f in filas]
                placeholders = ", ".join(["%s"] * len(ids))
                query_update = f"""
                    UPDATE cola_automatizacion
                    SET estado = 'en_proceso',
                        target_pc = %s,
                        instance_id = %s,
                        fecha_inicio = NOW()
                    WHERE id IN ({placeholders});
                """
                cursor.execute(query_update, [target_pc_id, instance_id] + ids)
                conn.commit()

                # Normalizar resultado
                resultado = []
                for f in filas:
                    resultado.append({
                        "id": f["id"],
                        "numero_de_linea": f.get("numero_de_linea"),
                        "dni": f.get("dni"),
                        "auto_id": f.get("auto_id") or target_auto_id,
                        "target_pc": target_pc_id,
                        "scraper_actual": scraper_actual or "telcos",
                        "datos": f.get("payload") or {}
                    })
                logger.info(f"📥 [VPS PULL] Reclamadas {len(resultado)} tareas de cola_automatizacion (auto_id='{target_auto_id}').")
                return resultado

            else:
                # Caso queue_registro_no_llame
                s_nombre = scraper_actual or "iris"
                query_find = f"""
                    SELECT id, ani, dni, estado, scraper_actual, fuente, datos_json
                    FROM {config.VPS_DB_TABLE} FORCE INDEX (idx_scraper_estado)
                    WHERE scraper_actual = %s AND estado = 'pendiente'
                    ORDER BY id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                """
                cursor.execute(query_find, (s_nombre, limit))
                filas = cursor.fetchall()

                if not filas:
                    conn.rollback()
                    return []

                ids = [f["id"] for f in filas]
                placeholders = ", ".join(["%s"] * len(ids))
                query_update = f"""
                    UPDATE {config.VPS_DB_TABLE}
                    SET estado = 'procesando',
                        fecha_modificacion = NOW()
                    WHERE id IN ({placeholders});
                """
                cursor.execute(query_update, ids)
                conn.commit()

                resultado = []
                for f in filas:
                    resultado.append({
                        "id": f["id"],
                        "numero_de_linea": f.get("ani"),
                        "ani": f.get("ani"),
                        "dni": f.get("dni"),
                        "auto_id": "registro_no_llame",
                        "target_pc": pc_id or getattr(config, "WORKER_PC_ID", "PC-00"),
                        "scraper_actual": f.get("scraper_actual") or s_nombre,
                        "datos": f.get("datos_json") or {}
                    })
                logger.info(f"📥 [VPS PULL] Reclamadas {len(resultado)} tareas de {config.VPS_DB_TABLE} (scraper='{s_nombre}').")
                return resultado

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"❌ Error en pull masivo desde VPS ({tipo_cola}): {e}")
            raise
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


    # -------------------------------------------------------------------------
    # Helpers: formateo canónico para cola_automatizacion (IRIS)
    # -------------------------------------------------------------------------

    @staticmethod
    def _to_snake_iris(text: str) -> str:
        """Normaliza el nombre de operación IRIS a snake_case."""
        t = str(text or "").strip().lower().replace("ñ", "n")
        t = re.sub(r"[^a-z0-9\s_]", "", t)
        t = re.sub(r"\s+", "_", t)
        return t.strip("_")

    @staticmethod
    def _agrupar_operaciones_iris(registros: list) -> dict:
        """
        Agrupa los registros históricos de IRIS por tipo de operación.
        Replica exactamente la lógica de ColaAutomatizacionAdapter._agrupar_operaciones_iris().

        Entrada : [{"operacion": "Altas", ...}, {"operacion": "Port Out", ...}]
        Salida  : {"altas": {"altas": {...}}, "port_out": {"port_out": {...}}}
                  o, si hay > 1 del mismo tipo:
                  {"altas": {"altas_1": {...}, "altas_2": {...}}}
        """
        from collections import defaultdict
        grupos: dict = defaultdict(list)
        for reg in registros:
            key = VPSSyncAdapter._to_snake_iris(reg.get("operacion", "desconocido"))
            grupos[key].append(reg)

        resultado = {}
        for clave, ops in grupos.items():
            if len(ops) == 1:
                resultado[clave] = {clave: ops[0]}
            else:
                resultado[clave] = {f"{clave}_{i + 1}": op for i, op in enumerate(ops)}
        return resultado

    def _formatear_iris_cola_auto(self, datos_totales: dict) -> dict:
        """
        Transforma el JSON acumulado de staging al formato canónico de
        cola_automatizacion.resultado para scrapers IRIS.

        Replica la lógica de ColaAutomatizacionAdapter._formatear_resultado()
        para que el Push nocturno produzca exactamente el mismo resultado que
        el path directo (sin staging).

        Estructura de entrada (datos_totales del SQLite local):
            {
              "enacom": {...},
              "iris": {
                "status": "coincidencia",
                "detalles": {"registros": [...], "total_operaciones": N},
                ...
              }
            }

        Estructura de salida (para columna `resultado` en VPS):
            {
              "enacom": {...},
              "iris": {
                "altas": {"altas_1": {...}},
                "port_out": {"port_out": {...}},
                "ultima_modificacion": "YYYY-MM-DD HH:MM:SS"
              }
            }
        """
        from datetime import datetime
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        resultado_final: dict = {}

        # Bloque ENACOM (pass-through)
        bloque_enacom = datos_totales.get("enacom")
        if isinstance(bloque_enacom, dict) and bloque_enacom:
            resultado_final["enacom"] = bloque_enacom

        # Bloque IRIS → agrupar operaciones
        datos_iris = datos_totales.get("iris") or {}
        if isinstance(datos_iris, dict):
            detalles = datos_iris.get("detalles") or {}
            raw_block = datos_iris.get("raw") or {}

            registros_hist = []
            if isinstance(detalles, dict) and "registros" in detalles:
                registros_hist = detalles["registros"]
            elif isinstance(raw_block, dict) and "registros" in raw_block:
                registros_hist = raw_block["registros"]

            es_coincidencia = str(datos_iris.get("status", "")).lower() in (
                "coincidencia", "completado", "success"
            )

            if es_coincidencia and registros_hist:
                iris_dict = self._agrupar_operaciones_iris(registros_hist)
                iris_dict["ultima_modificacion"] = ahora_str
                resultado_final["iris"] = iris_dict
            elif datos_iris:
                # Sin registros: guardar datos base sin blobs pesados
                iris_clean = {k: v for k, v in datos_iris.items() if k not in ("detalles", "raw")}
                iris_clean["ultima_modificacion"] = ahora_str
                resultado_final["iris"] = iris_clean

        # Cualquier otra fuente acumulada (claro, personal, movistar, datuar, etc.)
        for k, v in datos_totales.items():
            if k not in ("enacom", "iris", "iris_v2"):
                resultado_final[k] = v

        return resultado_final
    def subir_lote_vps(
        self,
        tipo_cola: str,
        lote: List[Dict[str, Any]]
    ) -> int:
        """
        Sube un chunk (máximo 5.000 filas) a MySQL VPS en una sola transacción atómica
        utilizando cursor.executemany().
        """
        if not lote:
            return 0

        tipo_cola_clean = (tipo_cola or "cola_automatizacion").lower().strip()
        conn = None
        cursor = None
        try:
            conn = self._get_connection(tipo_cola=tipo_cola_clean)
            conn.start_transaction()
            cursor = conn.cursor(buffered=True)

            if tipo_cola_clean == "cola_automatizacion":
                query_push = """
                    UPDATE cola_automatizacion
                    SET estado = %s,
                        resultado = %s,
                        error_msg = %s,
                        scrapers_intentados = %s,
                        dni = CASE WHEN (dni IS NULL OR dni = '') AND %s IS NOT NULL THEN %s ELSE dni END,
                        numero_de_linea = CASE WHEN (numero_de_linea IS NULL OR numero_de_linea = '') AND %s IS NOT NULL THEN %s ELSE numero_de_linea END,
                        fecha_fin = NOW()
                    WHERE id = %s;
                """
                params = []
                for item in lote:
                    st_local = str(item.get("estado_local", "")).lower()
                    estado_vps = "fallido" if st_local == "fallido" else "completado"
                    res_raw = item.get("resultado_json")
                    # SQLite almacena JSON como TEXT; parsear si viene como string
                    if isinstance(res_raw, str):
                        try:
                            res_dict = json.loads(res_raw)
                        except Exception:
                            res_dict = {}
                    elif isinstance(res_raw, dict):
                        res_dict = res_raw
                    else:
                        res_dict = {}
                    # Para IRIS: aplicar agrupamiento canónico de operaciones
                    if isinstance(res_dict, dict) and "iris" in res_dict:
                        res_dict = self._formatear_iris_cola_auto(res_dict)
                    res_str = json.dumps(res_dict, ensure_ascii=False) if res_dict else "{}"

                    err = item.get("error_msg")
                    scrapers = item.get("scrapers_intentados") or item.get("scraper_actual") or "telcos"
                    dni_val = str(item.get("dni") or "").strip() or None
                    linea_val = str(item.get("numero_de_linea") or "").strip() or None
                    id_vps = item["id_vps"]

                    params.append((
                        estado_vps,
                        res_str,
                        err,
                        scrapers,
                        dni_val, dni_val,
                        linea_val, linea_val,
                        id_vps
                    ))

                cursor.executemany(query_push, params)
                conn.commit()
                logger.info(f"📤 [VPS PUSH] Commited {len(params)} filas en cola_automatizacion (Chunk atómico de 5.000).")
                return len(params)

            else:
                # Caso queue_registro_no_llame
                query_push = f"""
                    UPDATE {config.VPS_DB_TABLE}
                    SET estado = %s,
                        scraper_actual = %s,
                        fuente = %s,
                        datos_json = %s,
                        dni = CASE WHEN (dni IS NULL OR dni = '') AND %s IS NOT NULL THEN %s ELSE dni END,
                        descripcion = %s,
                        latencia = %s,
                        fecha_modificacion = NOW()
                    WHERE id = %s;
                """
                params = []
                for item in lote:
                    st_local = str(item.get("estado_local", "")).lower()
                    estado_vps = "error" if st_local == "fallido" else "completado"
                    s_actual = item.get("scraper_actual") or "finalizado"
                    fuente_raw = item.get("fuente") or "[]"
                    if isinstance(fuente_raw, list):
                        fuente_str = json.dumps(fuente_raw, ensure_ascii=False)
                    else:
                        fuente_str = str(fuente_raw)

                    res_json = item.get("resultado_json")
                    if isinstance(res_json, (dict, list)):
                        res_str = json.dumps(res_json, ensure_ascii=False)
                    else:
                        res_str = str(res_json or "{}")

                    dni_val = str(item.get("dni") or "").strip() or None
                    desc = str(item.get("descripcion") or "")[:250]
                    lat = float(item.get("latencia", 0.0) or 0.0)
                    id_vps = item["id_vps"]

                    params.append((
                        estado_vps,
                        s_actual,
                        fuente_str,
                        res_str,
                        dni_val, dni_val,
                        desc,
                        lat,
                        id_vps
                    ))

                cursor.executemany(query_push, params)
                conn.commit()
                logger.info(f"📤 [VPS PUSH] Commited {len(params)} filas en {config.VPS_DB_TABLE} (Chunk atómico de 5.000).")
                return len(params)

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"❌ Error en push masivo a VPS ({tipo_cola}): {e}")
            raise
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


