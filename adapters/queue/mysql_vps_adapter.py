# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Repositorio de Cola MySQL 8 VPS
Implementa IColaRepositorioPort contra la tabla queue_registro_no_llame en el VPS central.
Aplica:
- Bloqueo de concurrencia atómico con FOR UPDATE SKIP LOCKED.
- Cascada de prioridades B-Tree (P1: 11/Mendoza, P2: Sur, P3: Resto).
- Segmentación por scraper_actual e índice idx_scraper_estado.
- Fusión y persistencia atómica de datos_json en lote.
"""
import os
import time
import json
import logging
from typing import List, Dict, Any, Optional
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

import config
from core.ports.queue_port import IColaRepositorioPort
from core.domain.entities import Linea, RegistroCola, Prioridad, EstadoRegistro

logger = logging.getLogger("MySQLQueueAdapter")

# Rangos numéricos B-Tree sobre columna ani (BIGINT de 10 dígitos)
SQL_RANGE_P1 = "((ani BETWEEN 1100000000 AND 1199999999) OR (ani BETWEEN 2600000000 AND 2639999999))"
SQL_RANGE_P2 = "((ani BETWEEN 2800000000 AND 2809999999) OR (ani BETWEEN 2900000000 AND 2999999999))"
SQL_RANGE_P3 = f"(ani BETWEEN 1000000000 AND 9999999999) AND NOT ({SQL_RANGE_P1} OR {SQL_RANGE_P2})"

PRIORIDADES_CONFIG = {
    1: {"nombre": "P1 (11 / Mendoza)", "filtro": SQL_RANGE_P1, "enum": Prioridad.P1_AMBA_MENDOZA},
    2: {"nombre": "P2 (Sur / Patagonia)", "filtro": SQL_RANGE_P2, "enum": Prioridad.P2_SUR_PATAGONIA},
    3: {"nombre": "P3 (Resto del País)", "filtro": SQL_RANGE_P3, "enum": Prioridad.P3_RESTO},
}

class MySQLQueueAdapter(IColaRepositorioPort):
    """Adaptador de producción para la base de datos MySQL 8 del VPS central."""

    def __init__(self, pool_size: int = 1, pool_name: Optional[str] = None):
        p_name = pool_name or f"mysql_queue_pool_{os.getpid()}"
        self.table = config.VPS_DB_TABLE
        self._prio_exhausted_until: Dict[int, float] = {1: 0.0, 2: 0.0}

        self.pool = MySQLConnectionPool(
            pool_name=p_name,
            pool_size=pool_size,
            host=config.VPS_DBHOST,
            port=config.VPS_DBPORT,
            user=config.VPS_DBUSER,
            password=config.VPS_DBPASS,
            database=config.VPS_DBNAME,
            autocommit=False,
            connection_timeout=20
        )

    def _get_connection(self, max_retries: int = 4, retry_delay: float = 2.0):
        for attempt in range(1, max_retries + 1):
            try:
                conn = self.pool.get_connection()
                conn.ping(reconnect=True, attempts=3, delay=1)
                return conn
            except Exception as e:
                logger.warning(f"Reintento {attempt}/{max_retries} de conexión a VPS: {e}")
                if attempt == max_retries:
                    raise
                time.sleep(retry_delay * (1.5 ** (attempt - 1)))

    def reservar_lote(
        self, 
        batch_size: int = 15, 
        prioridad: Optional[int] = None, 
        scraper_nombre: str = "iris",
        max_intentos: int = 3,
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        now = time.time()
        # Normalizar alias de scraper al nombre canónico
        nombre_clean = (scraper_nombre or "iris").lower().strip()
        if "iris" in nombre_clean:
            canon = "iris"
        elif "claro" in nombre_clean:
            canon = "claro"
        elif "personal" in nombre_clean:
            canon = "personal"
        elif "movistar" in nombre_clean:
            canon = "movistar"
        elif "cuit" in nombre_clean:
            canon = "cuitonline"
        elif "datuar" in nombre_clean:
            canon = "datuar"
        else:
            canon = nombre_clean

        if prioridad is not None:
            niveles = [prioridad]
        else:
            niveles = [
                n for n in [1, 2, 3] 
                if n not in self._prio_exhausted_until or now >= self._prio_exhausted_until[n]
            ]
            if not niveles:
                niveles = [1, 2, 3]

        for intento in range(1, max_intentos + 1):
            conn = None
            cursor = None
            try:
                conn = self._get_connection()
                cursor = conn.cursor(dictionary=True)
                filas = []

                for nivel in niveles:
                    cfg = PRIORIDADES_CONFIG.get(nivel)
                    if not cfg:
                        continue
                    
                    filtro_sql = cfg["filtro"]

                    # Selección con bloqueo SKIP LOCKED forzando índice idx_scraper_estado
                    select_query = f"""
                        SELECT id, ani, estado, scraper_actual, fuente, datos_json
                        FROM `{self.table}` FORCE INDEX (idx_scraper_estado)
                        WHERE scraper_actual = %s 
                          AND estado = 'pendiente' 
                          AND {filtro_sql}
                        ORDER BY id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    """
                    cursor.execute(select_query, (canon, batch_size))
                    filas = cursor.fetchall()
                    if filas:
                        prio_enum = cfg["enum"]
                        prio_nom = cfg["nombre"]
                        for r in filas:
                            r["prioridad_enum"] = prio_enum
                            r["prioridad_nombre"] = prio_nom
                        break
                    else:
                        if prioridad is None and nivel in self._prio_exhausted_until:
                            self._prio_exhausted_until[nivel] = time.time() + 45.0

                if not filas:
                    conn.commit()
                    return []

                ids = [r["id"] for r in filas]
                format_ids = ",".join(["%s"] * len(ids))

                # Marcar en 'procesando'
                update_query = f"""
                    UPDATE `{self.table}`
                    SET estado = 'procesando',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({format_ids})
                """
                cursor.execute(update_query, ids)
                conn.commit()

                # Transformar a Entidades de Dominio
                registros = []
                for r in filas:
                    try:
                        raw_json = json.loads(r.get("datos_json") or "{}")
                    except Exception:
                        raw_json = {}

                    registros.append(RegistroCola(
                        id=r["id"],
                        linea=Linea(str(r["ani"])),
                        prioridad=r["prioridad_enum"],
                        prioridad_nombre=r["prioridad_nombre"],
                        estado=EstadoRegistro.PROCESANDO,
                        scraper_actual=scraper_nombre,
                        fuente=r.get("fuente"),
                        datos_existentes=raw_json
                    ))

                return registros

            except Exception as e:
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                logger.warning(f"Error al reservar lote (intento {intento}/{max_intentos}): {e}")
                if intento == max_intentos:
                    return []
                time.sleep(1.5 * intento)
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
        return []

    def persistir_resultados(self, resultados: List[Dict[str, Any]], max_intentos: int = 3) -> bool:
        if not resultados:
            return True

        for intento in range(1, max_intentos + 1):
            conn = None
            cursor = None
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                query = f"""
                    UPDATE `{self.table}`
                    SET scraper_actual = %s,
                        estado = %s,
                        descripcion_scraper = %s,
                        fuente = %s,
                        datos_json = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """
                valores = []
                for r in resultados:
                    raw_data = r.get("datos")
                    json_str = json.dumps(raw_data, ensure_ascii=False) if isinstance(raw_data, (dict, list)) else raw_data

                    valores.append((
                        r.get("scraper_actual", "finalizado"),
                        r.get("estado", "pendiente"),
                        (r.get("descripcion") or "")[:195],
                        r.get("fuente", "[]"),
                        json_str,
                        r["id"]
                    ))

                try:
                    cursor.executemany(query, valores)
                    conn.commit()
                    return True
                except mysql.connector.errors.IntegrityError as ie:
                    # Si ocurre colisión en idx_unique por ANIs duplicados heredados,
                    # hacemos fallback fila por fila para aislar el conflicto sin perder el lote completo
                    conn.rollback()
                    logger.warning(f"IntegrityError en lote ({ie}). Aplicando persistencia individual fila por fila...")
                    for v in valores:
                        try:
                            cursor.execute(query, v)
                            conn.commit()
                        except Exception as item_err:
                            conn.rollback()
                            logger.error(f"Fila ID {v[5]} descartada por colisión de clave única: {item_err}")
                    return True
            except Exception as e:
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                logger.warning(f"Error persistiendo resultados en lote (intento {intento}/{max_intentos}): {e}")
                if intento == max_intentos:
                    return False
                time.sleep(1.5 * intento)
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
        return False

    def revertir_a_pendiente(self, ids: List[int], max_intentos: int = 3) -> bool:
        if not ids:
            return True

        for intento in range(1, max_intentos + 1):
            conn = None
            cursor = None
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                format_ids = ",".join(["%s"] * len(ids))
                query = f"""
                    UPDATE `{self.table}`
                    SET estado = 'pendiente',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({format_ids}) AND estado = 'procesando'
                """
                cursor.execute(query, ids)
                conn.commit()
                return True
            except Exception as e:
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                if intento == max_intentos:
                    return False
                time.sleep(1.5 * intento)
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
        return False

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        conn = None
        cursor = None
        afectados = 0
        lock_adquirido = False
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Mutex distribuido MySQL: GET_LOCK con timeout 0s (no-wait)
            # Solo 1 nodo entre todas las máquinas concurrentes ejecutará el barrido
            cursor.execute("SELECT GET_LOCK('watchdog_sweeper_mutex', 0)")
            row = cursor.fetchone()
            if not row or (isinstance(row, (list, tuple)) and row[0] != 1) or (isinstance(row, dict) and list(row.values())[0] != 1):
                logger.debug("Watchdog Sweeper: Mutex ya adquirido por otro nodo. Omitiendo ciclo.")
                return 0

            lock_adquirido = True

            query = f"""
                UPDATE `{self.table}`
                SET estado = 'pendiente',
                    updated_at = CURRENT_TIMESTAMP
                WHERE estado = 'procesando'
                  AND updated_at < NOW() - INTERVAL %s MINUTE
            """
            cursor.execute(query, (minutos_inactividad,))
            conn.commit()
            afectados = cursor.rowcount
            if afectados > 0:
                logger.warning(f"Watchdog Sweeper: {afectados} registros recuperados a 'pendiente'.")
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Error liberando huérfanos: {e}")
        finally:
            if lock_adquirido and cursor:
                try:
                    cursor.execute("SELECT RELEASE_LOCK('watchdog_sweeper_mutex')")
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
        return afectados

    def obtener_estadisticas(self) -> Dict[str, int]:
        conn = None
        cursor = None
        stats = {}
        try:
            conn = self._get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(f"""
                SELECT scraper_actual, estado, COUNT(*) as cant 
                FROM `{self.table}` 
                GROUP BY scraper_actual, estado
            """)
            for r in cursor.fetchall():
                s = r.get("scraper_actual") or "sin_scraper"
                e = r.get("estado") or "sin_estado"
                stats[f"{s}_{e}"] = r["cant"]
                stats[e] = stats.get(e, 0) + r["cant"]
        except Exception as e:
            logger.error(f"Error consultando estadísticas: {e}")
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
