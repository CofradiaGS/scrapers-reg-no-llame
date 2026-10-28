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
                timeout_lease_min = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)(scraper_actual or target_auto_id)

                # Fase 1: Reclamar prioritariamente registros en estado 'pendiente'
                # Se fuerza idx_cola_auto_estado (auto_id, estado) para evitar scans completos de millones de filas
                query_pendientes = """
                    SELECT id, numero_de_linea, dni, auto_id, target_pc, payload, prioridad, fuente
                    FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
                    WHERE auto_id = %s
                      AND estado = 'pendiente'
                      AND fecha_fin IS NULL
                      AND (resultado IS NULL OR resultado = '')
                      AND (target_pc IS NULL OR target_pc = '' OR FIND_IN_SET(%s, target_pc) > 0 OR target_pc = 'PC-00')
<<<<<<< Updated upstream
                    ORDER BY id ASC
=======
                      AND (
                          estado = 'pendiente'
                          OR (
                              estado = 'en_proceso'
                              AND (resultado IS NULL OR resultado = '')
                              AND fecha_inicio < NOW() - INTERVAL {timeout_lease_min} MINUTE
                          )
                      )
                    ORDER BY prioridad ASC, id ASC
>>>>>>> Stashed changes
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                """
                cursor.execute(query_pendientes, (target_auto_id, target_pc_id, limit))
                filas = cursor.fetchall() or []

                # Fase 2: Si no se completó el cupo solicitado, rescatar tareas en 'en_proceso' con lease vencido
                if len(filas) < limit:
                    remanente = limit - len(filas)
                    query_huerfanos = f"""
                        SELECT id, numero_de_linea, dni, auto_id, target_pc, payload, prioridad, fuente
                        FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
                        WHERE auto_id = %s
                          AND estado = 'en_proceso'
                          AND fecha_inicio < NOW() - INTERVAL {timeout_lease_min} MINUTE
                          AND fecha_fin IS NULL
                          AND (resultado IS NULL OR resultado = '')
                          AND (resultado IS NULL OR (resultado NOT LIKE '%coincidencia%' AND resultado NOT LIKE '%titular%'))
                          AND (target_pc IS NULL OR target_pc = '' OR FIND_IN_SET(%s, target_pc) > 0 OR target_pc = 'PC-00')
                        ORDER BY id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED;
                    """
                    cursor.execute(query_huerfanos, (target_auto_id, target_pc_id, remanente))
                    huerfanos = cursor.fetchall()
                    if huerfanos:
                        filas.extend(huerfanos)

                # Ingesta Automática Cobro Express -> ENACOM:
                # Si no hay tareas pendientes en el VPS para scraper_enacom, alimentar de inmediato
                # un nuevo lote de 5.000 tareas desde 'scraper_cobro_express' (sin coincidencia, 2 meses hábiles)
                # con prioridades geográficas (P1: BSS/Mendoza, P2: Sur/Cuyo, P3: Resto)
                if not filas and target_auto_id in ("scraper_enacom", "enacom"):
                    try:
                        conn.rollback()
                        logger.info(
                            f"ℹ️ [PULL ENACOM] 0 pendientes para '{target_auto_id}' en VPS. "
                            f"Alimentando lote de {limit:,} tareas desde 'scraper_cobro_express' (sin coincidencia, 2 meses)..."
                        )
                        from scripts.alimentar_enacom_desde_cobro_express import alimentar_enacom
                        nuevas = alimentar_enacom(
                            batch_size=limit,
                            target_pc=target_pc_id,
                            limit_total=limit,
                            silent=True
                        )
                        if nuevas > 0:
                            logger.info(f"✨ [PULL ENACOM] {nuevas:,} nuevas tareas insertadas con prioridades P1/P2/P3. Reclamando lote...")
                            conn.start_transaction()
                            cursor.execute(query_find, (target_auto_id, target_pc_id, limit))
                            filas = cursor.fetchall()
                    except Exception as e_ce:
                        logger.error(f"Error al alimentar ENACOM desde Cobro Express: {e_ce}", exc_info=True)

                if not filas:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    return []

                ids = [f["id"] for f in filas]
<<<<<<< Updated upstream
                # Actualización en chunks de 1000 para no agotar el buffer de bloqueos de InnoDB (Error 1206)
                chunk_size = 1000
                for i in range(0, len(ids), chunk_size):
                    chunk_ids = ids[i:i + chunk_size]
=======
                # Micro-chunking de 500 IDs para evitar Error 1206 (Total number of locks exceeds lock table size)
                sub_chunk = 500
                for i in range(0, len(ids), sub_chunk):
                    chunk_ids = ids[i:i + sub_chunk]
>>>>>>> Stashed changes
                    placeholders = ", ".join(["%s"] * len(chunk_ids))
                    query_update = f"""
                        UPDATE cola_automatizacion
                        SET estado = 'en_proceso',
                            target_pc = %s,
                            instance_id = %s,
                            fecha_inicio = NOW()
<<<<<<< Updated upstream
                        WHERE id IN ({placeholders})
                          AND estado != 'completado'
                          AND estado NOT IN ('completado', 'finalizado')
                          AND fecha_fin IS NULL;
=======
                        WHERE id IN ({placeholders});
>>>>>>> Stashed changes
                    """
                    cursor.execute(query_update, [target_pc_id, instance_id] + chunk_ids)
                conn.commit()

                # Doble Buffer Autónomo (Always-Prefetched Pipeline):
                # El sistema debe mantener siempre tareas en 'en_proceso' (las que acabamos de pasar)
                # Y tareas de reserva en 'pendiente' en el VPS para que al pasar a completado, la siguiente posta esté lista.
                if target_auto_id in ("scraper_enacom", "enacom"):
                    try:
                        cursor.execute("""
                            SELECT COUNT(*) AS cant
                            FROM cola_automatizacion
                            WHERE auto_id = %s AND estado = 'pendiente';
                        """, (target_auto_id,))
                        cnt_row = cursor.fetchone()
                        pendientes_en_vps = cnt_row["cant"] if cnt_row else 0
                        if pendientes_en_vps < limit:
                            deficit = limit - pendientes_en_vps
                            logger.info(
                                f"🔄 [DOBLE BUFFER ENACOM] {len(filas):,} tareas pasadas a 'en_proceso'. "
                                f"Pendientes en VPS: {pendientes_en_vps:,} < {limit:,}. "
                                f"Reclamando e insertando {deficit:,} registros más desde Cobro Express (P1 -> P2 -> P3)..."
                            )
                            from scripts.alimentar_enacom_desde_cobro_express import alimentar_enacom
                            nuevas_buf = alimentar_enacom(
                                batch_size=deficit,
                                target_pc=target_pc_id,
                                limit_total=deficit,
                                silent=True
                            )
                            logger.info(f"✨ [DOBLE BUFFER ENACOM] {nuevas_buf:,} nuevas tareas insertadas como 'pendiente' en VPS. Buffer de reserva listo.")
                    except Exception as e_buf:
                        logger.warning(f"Aviso en recarga de doble buffer ENACOM: {e_buf}")

                # Normalizar resultado
                resultado = []
                for f in filas:
                    sc_dest = scraper_actual or "telcos"
                    if "enacom" in sc_dest.lower():
                        sc_dest = "enacom_web"
                    resultado.append({
                        "id": f["id"],
                        "numero_de_linea": f.get("numero_de_linea"),
                        "dni": f.get("dni"),
                        "auto_id": f.get("auto_id") or target_auto_id,
                        "target_pc": target_pc_id,
<<<<<<< Updated upstream
                        "scraper_actual": scraper_actual or "telcos",
                        "fuente": f.get("fuente") or f.get("auto_id") or target_auto_id,
=======
                        "scraper_actual": sc_dest,
                        "fuente": f.get("auto_id") or target_auto_id,
>>>>>>> Stashed changes
                        "tipo_cola": "cola_automatizacion",
                        "datos": f.get("payload") or {}
                    })
                logger.info(f"📥 [VPS PULL] Reclamadas {len(resultado)} tareas de cola_automatizacion (auto_id='{target_auto_id}').")
                return resultado

            else:
                # Caso queue_registro_no_llame
                s_nombre = scraper_actual or "iris"
                s_lower = (s_nombre or "").lower()
                if "iris" in s_lower:
                    s_nombre = "iris"
                elif "enacom" in s_lower:
                    s_nombre = "enacom_web"
                timeout_lease_min = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)(s_nombre)
                if s_nombre == "enacom_web":
                    query_find = f"""
                        SELECT id, ani, dni, estado, scraper_actual, fuente, datos_json, active
                        FROM {config.VPS_DB_TABLE}
                        WHERE estado = 'no_coincidencia'
                          AND (active IS NULL OR active = 0)
                          AND (datos_json NOT LIKE '%"enacom_web"%' OR datos_json IS NULL)
                        ORDER BY id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED;
                    """
                    cursor.execute(query_find, (limit,))
                    filas = cursor.fetchall()
                elif s_nombre in ("telcos", "telco", "telco_cascade"):
                    cond_lease = f"""
                        (estado = 'pendiente' OR (
                            estado = 'procesando'
                            AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL {timeout_lease_min} MINUTE)
                        ))
                    """
                    # Estrategia Telcos: Priorizar registros con DNI (Claro + Personal + Movistar).
                    # Solo si no cubren el limit, completar con registros sin DNI (Personal + Movistar).
                    query_con_dni = f"""
                        SELECT id, ani, dni, estado, scraper_actual, fuente, datos_json, active
                        FROM {config.VPS_DB_TABLE} FORCE INDEX (idx_scraper_estado)
                        WHERE scraper_actual IN ('telcos', 'claro', 'personal', 'movistar') 
                          AND {cond_lease}
                          AND (active IS NULL OR active = 0)
                          AND (dni IS NOT NULL AND dni != '')
                        ORDER BY id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED;
                    """
                    cursor.execute(query_con_dni, (limit,))
                    filas = cursor.fetchall()
                    if len(filas) < limit:
                        remanente = limit - len(filas)
                        query_sin_dni = f"""
                            SELECT id, ani, dni, estado, scraper_actual, fuente, datos_json, active
                            FROM {config.VPS_DB_TABLE} FORCE INDEX (idx_scraper_estado)
                            WHERE scraper_actual IN ('telcos', 'claro', 'personal', 'movistar') 
                              AND {cond_lease}
                              AND (active IS NULL OR active = 0)
                              AND (dni IS NULL OR dni = '')
                            ORDER BY id ASC
                            LIMIT %s
                            FOR UPDATE SKIP LOCKED;
                        """
                        cursor.execute(query_sin_dni, (remanente,))
                        filas.extend(cursor.fetchall())
                else:
                    cond_lease = f"""
                        (estado = 'pendiente' OR (
                            estado = 'procesando'
                            AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL {timeout_lease_min} MINUTE)
                        ))
                    """
                    cond_iris = "AND (fuente IS NULL OR fuente NOT LIKE '%%iris%%')" if "iris" in s_nombre.lower() else ""
                    query_find = f"""
                        SELECT id, ani, dni, estado, scraper_actual, fuente, datos_json, active
                        FROM {config.VPS_DB_TABLE} FORCE INDEX (idx_scraper_estado)
                        WHERE scraper_actual = %s AND {cond_lease} AND (active IS NULL OR active = 0) {cond_iris}
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
                # Micro-chunking de 500 IDs para evitar Error 1206 (Total number of locks exceeds lock table size)
                sub_chunk = 500
                for i in range(0, len(ids), sub_chunk):
                    chunk_ids = ids[i:i + sub_chunk]
                    placeholders = ", ".join(["%s"] * len(chunk_ids))
                    if s_nombre == "enacom_web":
                        query_update = f"""
                            UPDATE {config.VPS_DB_TABLE}
                            SET estado = 'procesando',
                                scraper_actual = 'enacom_web',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id IN ({placeholders});
                        """
                        cursor.execute(query_update, chunk_ids)
                    else:
                        query_update = f"""
                            UPDATE {config.VPS_DB_TABLE}
                            SET estado = 'procesando',
                                scraper_actual = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id IN ({placeholders});
                        """
                        cursor.execute(query_update, [s_nombre] + chunk_ids)
                conn.commit()

                resultado = []
                for f in filas:
                    datos_raw = f.get("datos_json") or {}
                    if isinstance(datos_raw, str):
                        try:
                            datos_dict = json.loads(datos_raw)
                        except Exception:
                            datos_dict = {}
                    elif isinstance(datos_raw, dict):
                        datos_dict = datos_raw
                    else:
                        datos_dict = {}

                    if s_nombre in ("enacom_web", "scraper_enacom", "enacom"):
                        sc_destino = "enacom_web"
                    else:
                        # Regla estricta exclusiva para queue_registro_no_llame:
                        # Todo registro debe pasar primero por IRIS a no ser que ya haya sido scrapeado por IRIS.
                        iris_info = datos_dict.get("iris", {})
                        iris_ya_scrapeado = isinstance(iris_info, dict) and bool(iris_info.get("status"))

                        if not iris_ya_scrapeado:
                            sc_destino = "iris"
                        else:
                            sc_destino = "telcos" if s_nombre == "telcos" else (f.get("scraper_actual") or s_nombre)

                    resultado.append({
                        "id": f["id"],
                        "numero_de_linea": f.get("ani"),
                        "ani": f.get("ani"),
                        "dni": f.get("dni"),
                        "auto_id": "registro_no_llame",
                        "target_pc": pc_id or getattr(config, "WORKER_PC_ID", "PC-00"),
                        "scraper_actual": sc_destino,
                        "fuente": f.get("fuente"),
                        "tipo_cola": "registro_no_llame",
                        "active": bool(f.get("active")) if f.get("active") is not None else None,
                        "datos": datos_dict
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

        # Bloque DATUAR
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

        # Bloque CUITONLINE
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

        # Bloque BCRA
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

        # Cualquier otra fuente acumulada (claro, personal, movistar, etc.)
        for k, v in datos_totales.items():
            if k not in ("enacom", "iris", "iris_v2", "datuar", "cuitonline", "bcra"):
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
        Protegido por semáforo distribuido MySQL (GET_LOCK) para evitar saturación concurrente
        entre múltiples PCs en la red.
        """
        if not lote:
            return 0

        tipo_cola_clean = (tipo_cola or "cola_automatizacion").lower().strip()
        conn = None
        cursor = None
        lock_adquirido = False
        use_semaphore = getattr(config, "SYNC_PUSH_SEMAPHORE_ENABLED", True)
        lock_name = getattr(config, "SYNC_PUSH_LOCK_NAME", "vps_push_nocturno_semaphore")
        lock_timeout = getattr(config, "SYNC_PUSH_LOCK_TIMEOUT", 600)

        try:
            conn = self._get_connection(tipo_cola=tipo_cola_clean)
            cursor = conn.cursor(buffered=True)

            if use_semaphore:
                logger.info(
                    f"⏳ [SEMAFORO VPS] Solicitando turno exclusivo '{lock_name}' "
                    f"(esperando hasta {lock_timeout}s en cola si otra PC está subiendo)..."
                )
                t_lock_start = time.time()
                cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, lock_timeout))
                lock_row = cursor.fetchone()
                lock_res = lock_row[0] if lock_row else None
                if lock_res != 1:
                    raise TimeoutError(
                        f"No se pudo adquirir el semáforo '{lock_name}' tras {lock_timeout}s de espera en cola. "
                        f"Resultado MySQL: {lock_res}"
                    )
                lock_adquirido = True
                wait_time = round(time.time() - t_lock_start, 2)
                logger.info(
                    f"🟢 [SEMAFORO VPS] Turno adquirido para chunk de {len(lote):,} registros "
                    f"(tiempo de espera en cola: {wait_time}s)."
                )

            conn.start_transaction()

            if tipo_cola_clean == "cola_automatizacion":
                items_prepared = []
                for item in lote:
                    st_local = str(item.get("estado_local", "")).lower()
                    estado_vps = "fallido" if st_local == "fallido" else "completado"
                    res_raw = item.get("resultado_json")
                    if isinstance(res_raw, str):
                        try:
                            res_dict = json.loads(res_raw)
                        except Exception:
                            res_dict = {}
                    elif isinstance(res_raw, dict):
                        res_dict = res_raw
                    else:
                        res_dict = {}
                    if isinstance(res_dict, dict) and "iris" in res_dict:
                        res_dict = self._formatear_iris_cola_auto(res_dict)
                    elif isinstance(res_dict, dict) and "enacom" in res_dict:
                        # El bloque local 'enacom' solo debe incluirse cuando el registro pasa por IRIS
                        res_dict = {k: v for k, v in res_dict.items() if k != "enacom"}
                    res_str = json.dumps(res_dict, ensure_ascii=False) if res_dict else "{}"

                    err = item.get("error_msg")
                    scrapers = item.get("scrapers_intentados") or item.get("scraper_actual") or "telcos"
                    dni_val = str(item.get("dni") or "").strip() or None
                    if not dni_val and isinstance(res_dict, dict):
                        from core.domain.entities import ReglaPipeline
                        dni_val = ReglaPipeline.extraer_dni(res_dict)
                    linea_val = str(item.get("numero_de_linea") or "").strip() or None
                    id_vps = item["id_vps"]

                    items_prepared.append((id_vps, estado_vps, res_str, err, scrapers, dni_val, linea_val))

                # Batch UPDATE atómico de ultra-alta velocidad usando JOIN VALUES de MySQL 8 (1 solo round-trip por bloque de 500)
                batch_size = 500
                total_afectados = 0
                for b_i in range(0, len(items_prepared), batch_size):
                    sub_lote = items_prepared[b_i:b_i + batch_size]
                    row_placeholders = ", ".join(["ROW(%s, %s, %s, %s, %s, %s, %s)"] * len(sub_lote))
                    sub_params = []
                    for id_vps, est, res, err, sc, dni, lin in sub_lote:
                        sub_params.extend([id_vps, est, res, err, sc, dni, lin])

                    query_push_batch = f"""
                        UPDATE cola_automatizacion c
                        JOIN (VALUES {row_placeholders}) AS t(id, estado, resultado, error_msg, scrapers_intentados, dni, numero_de_linea)
                        ON c.id = t.id
                        SET c.estado = t.estado,
                            c.resultado = t.resultado,
                            c.error_msg = t.error_msg,
                            c.scrapers_intentados = t.scrapers_intentados,
                            c.dni = CASE WHEN (c.dni IS NULL OR c.dni = '') AND t.dni IS NOT NULL THEN t.dni ELSE c.dni END,
                            c.numero_de_linea = CASE WHEN (c.numero_de_linea IS NULL OR c.numero_de_linea = '') AND t.numero_de_linea IS NOT NULL THEN t.numero_de_linea ELSE c.numero_de_linea END,
                            c.fecha_fin = NOW();
                    """
                    cursor.execute(query_push_batch, sub_params)
                    total_afectados += len(sub_lote)

                conn.commit()
                logger.info(f"📤 [VPS PUSH] Commited {total_afectados} filas en cola_automatizacion (Batch turbo JOIN VALUES).")
                return total_afectados

            else:
                # Caso queue_registro_no_llame
                items_prepared = []
                for item in lote:
                    st_local = str(item.get("estado_local", "")).lower()
                    s_raw = item.get("scraper_actual") or "finalizado"
                    if s_raw in ("enacom_web", "scraper_enacom", "enacom"):
                        estado_vps = "no_coincidencia" if st_local == "fallido" else "completado"
                        s_actual = "finalizado"
                    else:
                        estado_vps = "error" if st_local == "fallido" else "completado"
                        s_actual = s_raw
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

                    dni_val = item.get("dni")
                    if not dni_val and isinstance(res_json, dict):
                        from core.domain.entities import ReglaPipeline
                        dni_val = ReglaPipeline.extraer_dni(res_json)
                    desc = str(item.get("descripcion") or item.get("descripcion_scraper") or "")[:195]
                    id_vps = item["id_vps"]

                    items_prepared.append((id_vps, estado_vps, s_actual, fuente_str, res_str, dni_val, desc))

                batch_size = 500
                total_afectados = 0
                for b_i in range(0, len(items_prepared), batch_size):
                    sub_lote = items_prepared[b_i:b_i + batch_size]
                    row_placeholders = ", ".join(["ROW(%s, %s, %s, %s, %s, %s, %s)"] * len(sub_lote))
                    sub_params = []
                    for id_vps, est, sc, fuente, res, dni, desc in sub_lote:
                        sub_params.extend([id_vps, est, sc, fuente, res, dni, desc])

                    query_push_batch = f"""
                        UPDATE {config.VPS_DB_TABLE} q
                        JOIN (VALUES {row_placeholders}) AS t(id, estado, scraper_actual, fuente, datos_json, dni, descripcion_scraper)
                        ON q.id = t.id
                        SET q.estado = t.estado,
                            q.scraper_actual = t.scraper_actual,
                            q.fuente = t.fuente,
                            q.datos_json = t.datos_json,
                            q.dni = CASE WHEN (q.dni IS NULL OR q.dni = '') AND t.dni IS NOT NULL THEN t.dni ELSE q.dni END,
                            q.descripcion_scraper = t.descripcion_scraper,
                            q.updated_at = CURRENT_TIMESTAMP;
                    """
                    cursor.execute(query_push_batch, sub_params)
                    total_afectados += len(sub_lote)

                conn.commit()
                logger.info(f"📤 [VPS PUSH] Commited {total_afectados} filas en {config.VPS_DB_TABLE} (Batch turbo JOIN VALUES).")
                return total_afectados

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"❌ Error en push masivo a VPS ({tipo_cola}): {e}")
            raise
        finally:
            if lock_adquirido and cursor:
                try:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    cursor.fetchone()
                    logger.info(f"⚪ [SEMAFORO VPS] Turno liberado exitosamente (turno disponible para otra PC).")
                except Exception as e_rel:
                    logger.warning(f"Aviso al liberar semáforo VPS: {e_rel}")
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

    def liberar_huerfanos_vps(
        self,
        tipo_cola: str,
        minutos_inactividad: Optional[int] = None,
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        scraper_actual: Optional[str] = None
    ) -> int:
        """
        Recupera registros colgados en 'en_proceso' (cola_automatizacion) o 'procesando' (queue_registro_no_llame)
        sin resultado en el VPS central, devolviéndolos atómicamente a 'pendiente'.
        Aplica un umbral de 12 horas (720 min por defecto) para proteger scrapers lentos.
        Utiliza un Mutex Distribuido (GET_LOCK) para prevenir condiciones de carrera entre nodos concurrentes.
        Retorna la cantidad de registros rescatados.
        """
        tipo_cola_clean = (tipo_cola or "cola_automatizacion").lower().strip()
        conn = None
        cursor = None
        lock_adquirido = False
        target_auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "telco_scraper")
        target_pc_id = pc_id or getattr(config, "WORKER_PC_ID", "PC-00")
        s_nombre = scraper_actual or ("telcos" if tipo_cola_clean == "cola_automatizacion" else "iris")
        lock_name = f"vps_sweeper_mutex_{tipo_cola_clean}_{target_auto_id if tipo_cola_clean == 'cola_automatizacion' else s_nombre}"

        if minutos_inactividad is None:
            minutos_inactividad = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)(s_nombre)

        try:
            conn = self._get_connection(tipo_cola=tipo_cola_clean)
            cursor = conn.cursor(buffered=True)

            # Mutex Distribuido con timeout 0s (no-wait): si otro nodo está barriendo, saltear sin esperar
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
            row = cursor.fetchone()
            if not row or (isinstance(row, (list, tuple)) and row[0] != 1) or (isinstance(row, dict) and list(row.values())[0] != 1):
                logger.debug(f"Watchdog VPS ({tipo_cola_clean}): Mutex '{lock_name}' ya adquirido por otro nodo. Omitiendo barrido.")
                return 0

            lock_adquirido = True
            liberados = 0

            if tipo_cola_clean == "cola_automatizacion":
                query = """
                    UPDATE cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
                    SET estado = 'pendiente',
                        instance_id = NULL
                    WHERE auto_id = %s
                      AND estado = 'en_proceso'
                      AND estado != 'completado'
                      AND estado NOT IN ('completado', 'finalizado')
                      AND (resultado IS NULL OR resultado = '')
                      AND (resultado IS NULL OR (resultado NOT LIKE '%coincidencia%' AND resultado NOT LIKE '%titular%'))
                      AND fecha_fin IS NULL
                      AND (
                          (target_pc = %s AND fecha_inicio < NOW() - INTERVAL %s MINUTE)
                          OR fecha_inicio < NOW() - INTERVAL %s MINUTE
                      )
                    LIMIT 2000;
                """
                # Para la máquina actual usamos minutos_inactividad (ej. 720m = 12h), para cualquier otra máquina 24h
                timeout_otras_pcs = max(minutos_inactividad * 2, minutos_inactividad + 60)
                cursor.execute(query, (target_auto_id, target_pc_id, minutos_inactividad, timeout_otras_pcs))
                conn.commit()
                liberados = cursor.rowcount
            else:
                # queue_registro_no_llame
                if s_nombre in ("telcos", "telco", "telco_cascade", "claro", "personal", "movistar"):
                    scrapers_filter = "('telcos', 'claro', 'personal', 'movistar')"
                else:
                    scrapers_filter = f"('{s_nombre}')"

                query = f"""
                    UPDATE {config.VPS_DB_TABLE}
                    SET estado = 'pendiente',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE estado = 'procesando'
                      AND estado != 'completado'
                      AND estado NOT IN ('completado', 'finalizado')
                      AND scraper_actual != 'finalizado'
                      AND (datos_json IS NULL OR datos_json = '' OR (datos_json NOT LIKE '%coincidencia%' AND datos_json NOT LIKE '%titular%'))
                      AND scraper_actual IN {scrapers_filter}
                      AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL %s MINUTE)
                """
                cursor.execute(query, (minutos_inactividad,))
                conn.commit()
                liberados = cursor.rowcount

            if liberados > 0:
                logger.info(f"🧹 [WATCHDOG VPS] Rescatados {liberados:,} registros huérfanos a estado 'pendiente' en VPS ({tipo_cola_clean}).")
            return liberados

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"❌ Error en barrido de huérfanos VPS ({tipo_cola_clean}): {e}")
            return 0
        finally:
            if lock_adquirido and cursor:
                try:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    cursor.fetchone()
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

    def sanitizar_pendientes_invalidos_vps(
        self,
        tipo_cola: str,
        auto_id: Optional[str] = None,
        scraper_actual: Optional[str] = None
    ) -> int:
        """
        Pasa a 'completado' con 'sin_coincidencia' directamente en el VPS cualquier tarea
        pendiente o en_proceso de Datuar que carezca de DNI o pertenezca a personas jurídicas (CUIT 30/33/34).
        """
        s_nombre = (scraper_actual or "").lower().strip()
        target_auto_id = auto_id or "scraper_datuar"
        if "datuar" not in s_nombre and "datuar" not in target_auto_id.lower():
            return 0

        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            query = """
                UPDATE cola_automatizacion
                SET estado = 'completado',
                    resultado = %s,
                    scrapers_intentados = CASE 
                        WHEN scrapers_intentados IS NULL OR scrapers_intentados = '' THEN %s
                        WHEN FIND_IN_SET(%s, scrapers_intentados) > 0 THEN scrapers_intentados
                        ELSE CONCAT(scrapers_intentados, ',', %s)
                    END,
                    fecha_fin = NOW()
                WHERE auto_id = %s
                  AND estado IN ('pendiente', 'en_proceso')
                  AND (
                      (dni IS NULL OR dni = '' OR dni = '0')
                      OR ((dni LIKE '30%' OR dni LIKE '33%' OR dni LIKE '34%') AND LENGTH(dni) = 11)
                  );
            """
            payload = json.dumps({"status": "sin_coincidencia", "motivo": "formato_invalido_omitido"}, ensure_ascii=False)
            cursor.execute(query, (payload, target_auto_id, target_auto_id, target_auto_id, target_auto_id))
            afectados = cursor.rowcount
            conn.commit()
            if afectados > 0:
                logger.info(f"🧹 [AUTO-SANITIZADOR DATUAR] {afectados:,} registros inválidos (sin DNI o empresa) pasados a 'completado' sin coincidencia en VPS.")
            return afectados
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.warning(f"Aviso en sanitización automática de cola VPS: {e}")
            return 0
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



