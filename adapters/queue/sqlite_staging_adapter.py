# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Staging Local SQLite en Modo WAL
Arquitectura Hexagonal - Puerto IColaRepositorioPort

Almacena tareas en disco local SSD de forma desacoplada para evitar I/O continuo
y sobrecarga de binlogs en el VPS central. Permite:
- Reclamo y persistencia local a 0ms de latencia de red.
- Concurrencia segura con PRAGMA journal_mode = WAL.
- Reabastecimiento automático matutino y push nocturno en lotes de a 5.000.
- Soporte polimórfico para 'cola_automatizacion' y 'queue_registro_no_llame'.
"""
import os
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import config
from core.ports.queue_port import IColaRepositorioPort
from core.ports.sync_port import ISyncLocalRepoPort
from core.domain.entities import Linea, RegistroCola, Prioridad, EstadoRegistro

logger = logging.getLogger("SQLiteStagingAdapter")


class SQLiteStagingAdapter(IColaRepositorioPort, ISyncLocalRepoPort):
    """Adaptador de persistencia local en SQLite (WAL) para Staging Offline-First."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        tipo_cola: str = "cola_automatizacion",
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        timeout: float = 30.0
    ):
        self.db_path = Path(db_path or getattr(config, "LOCAL_STAGING_DB_PATH", "data/staging_local.db")).resolve()
        self.tipo_cola = tipo_cola
        self.auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "telco_scraper")
        self.pc_id = pc_id or getattr(config, "WORKER_PC_ID", "PC-00")
        self.timeout = timeout

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar_esquema()

    def _get_connection(self) -> sqlite3.Connection:
        """Abre conexión local con soporte WAL y timeout para concurrencia."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout,
            isolation_level=None  # autocommit controlado manualmente con transacciones
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -64000;")  # 64 MB caché RAM
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)};")
        return conn

    def _inicializar_esquema(self):
        """Crea la tabla polimórfica tareas_staging e índices si no existen."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tareas_staging (
                    id_vps BIGINT NOT NULL,
                    tipo_cola TEXT NOT NULL,
                    numero_de_linea TEXT NOT NULL,
                    dni TEXT,
                    auto_id TEXT,
                    target_pc TEXT NOT NULL,
                    scraper_actual TEXT,
                    estado_local TEXT NOT NULL DEFAULT 'pendiente',
                    payload_origen TEXT,
                    resultado_json TEXT,
                    fuente TEXT,
                    scrapers_intentados TEXT,
                    descripcion TEXT,
                    latencia REAL DEFAULT 0.0,
                    error_msg TEXT,
                    reintentos_sync INTEGER DEFAULT 0,
                    fecha_descarga DATETIME DEFAULT CURRENT_TIMESTAMP,
                    fecha_procesado DATETIME,
                    fecha_sincronizado DATETIME,
                    PRIMARY KEY (id_vps, tipo_cola)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_tipo_estado ON tareas_staging(tipo_cola, estado_local);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_sincro ON tareas_staging(estado_local, fecha_sincronizado);")
            conn.execute("COMMIT;")
        finally:
            conn.close()


    def reservar_lote(
        self,
        batch_size: int = 20,
        prioridad: Optional[int] = None,
        scraper_nombre: str = "telcos",
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        """
        Reclama atómicamente un lote de tareas locales en estado 'pendiente'
        y las pasa de inmediato a 'en_proceso'.
        """
        registros: List[RegistroCola] = []
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.execute("""
                SELECT id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, 
                       scraper_actual, payload_origen, resultado_json
                FROM tareas_staging
                WHERE estado_local = 'pendiente' AND tipo_cola = ?
                ORDER BY id_vps ASC
                LIMIT ?;
            """, (self.tipo_cola, batch_size))
            filas = cursor.fetchall()

            if not filas:
                conn.execute("COMMIT;")
                return []

            ids_vps = [f["id_vps"] for f in filas]
            placeholders = ",".join("?" for _ in ids_vps)

            conn.execute(f"""
                UPDATE tareas_staging
                SET estado_local = 'en_proceso'
                WHERE tipo_cola = ? AND id_vps IN ({placeholders});
            """, [self.tipo_cola] + ids_vps)
            conn.execute("COMMIT;")

            for f in filas:
                num_linea = "".join(filter(str.isdigit, str(f["numero_de_linea"] or "")))
                dni_val = "".join(filter(str.isdigit, str(f["dni"] or ""))) if f["dni"] else None

                payload_dict = {}
                raw_payload = f["payload_origen"]
                if raw_payload:
                    try:
                        payload_dict = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                    except Exception:
                        payload_dict = {}

                linea_ent = Linea(ani=num_linea, dni=dni_val)
                reg = RegistroCola(
                    id=f["id_vps"],
                    linea=linea_ent,
                    prioridad=Prioridad.P2_SUR_PATAGONIA,
                    prioridad_nombre="P2 (Staging Local)",
                    estado=EstadoRegistro.PROCESANDO,
                    scraper_actual=f["scraper_actual"] or scraper_nombre,
                    fuente=f["tipo_cola"],
                    datos_existentes=payload_dict
                )
                registros.append(reg)

            return registros
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error(f"Error al reservar lote en SQLite local: {e}")
            return []
        finally:
            conn.close()

    def persistir_resultados(self, resultados: List[Dict[str, Any]]) -> bool:
        """
        Persiste los resultados procesados en SQLite local, marcándolos como 'listo_para_subir'.
        Almacena el 100% del JSON y metadatos sin tocar la base de datos central.
        """
        if not resultados:
            return True

        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            valores = []
            for item in resultados:
                id_vps = item["id"]
                dni_item = str(item.get("dni") or "").strip()
                status_raw = str(item.get("status", "")).lower()
                scraper_actual = str(item.get("scraper_actual") or "")
                fuente = item.get("fuente") or "[]"
                if isinstance(fuente, list):
                    fuente = json.dumps(fuente, ensure_ascii=False)
                desc = str(item.get("descripcion") or "")[:250]
                lat = float(item.get("latencia", 0.0) or 0.0)

                datos_totales = item.get("datos")
                if isinstance(datos_totales, (dict, list)):
                    try:
                        res_json_str = json.dumps(datos_totales, ensure_ascii=False)
                    except Exception as e_ser:
                        logger.warning(f"Aviso de serialización JSON en ID {id_vps}: {e_ser}. Aplicando sanitización...")
                        res_json_str = json.dumps(str(datos_totales), ensure_ascii=False)
                elif isinstance(datos_totales, str):
                    res_json_str = datos_totales
                else:
                    res_json_str = "{}"

                # Extraer lista de scrapers ejecutados para scrapers_intentados
                intentados_lista = []
                if isinstance(datos_totales, dict):
                    for telco in ("claro", "personal", "movistar", "iris", "datuar", "cuitonline"):
                        if telco in datos_totales:
                            intentados_lista.append(telco)
                scrapers_intentados = ",".join(intentados_lista) if intentados_lista else scraper_actual

                if status_raw in ("error", "failed", "fallido"):
                    estado_local = "fallido"
                    error_msg = desc or "Error en procesamiento local"
                else:
                    estado_local = "listo_para_subir"
                    error_msg = None

                valores.append((
                    estado_local,
                    res_json_str,
                    error_msg,
                    scrapers_intentados,
                    dni_item, dni_item,
                    desc,
                    lat,
                    scraper_actual,
                    fuente,
                    id_vps,
                    self.tipo_cola
                ))

            conn.executemany("""
                UPDATE tareas_staging
                SET estado_local = ?,
                    resultado_json = ?,
                    error_msg = ?,
                    scrapers_intentados = ?,
                    dni = CASE WHEN (dni IS NULL OR dni = '') AND ? != '' THEN ? ELSE dni END,
                    descripcion = ?,
                    latencia = ?,
                    scraper_actual = ?,
                    fuente = ?,
                    fecha_procesado = CURRENT_TIMESTAMP
                WHERE id_vps = ? AND tipo_cola = ?;
            """, valores)
            conn.execute("COMMIT;")
            return True
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error(f"Error al persistir resultados en SQLite local: {e}")
            return False
        finally:
            conn.close()

    def revertir_a_pendiente(self, ids: List[int]) -> bool:
        """Devuelve tareas de 'en_proceso' al estado 'pendiente' local."""
        if not ids:
            return True
        conn = self._get_connection()
        try:
            placeholders = ",".join("?" for _ in ids)
            with conn:
                conn.execute(f"""
                    UPDATE tareas_staging
                    SET estado_local = 'pendiente'
                    WHERE tipo_cola = ? AND estado_local = 'en_proceso' AND id_vps IN ({placeholders});
                """, [self.tipo_cola] + ids)
            return True
        except Exception as e:
            logger.error(f"Error al revertir a pendiente en SQLite: {e}")
            return False
        finally:
            conn.close()

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        """Recupera registros colgados en 'en_proceso' tras caídas imprevistas o reinicios."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute("""
                    UPDATE tareas_staging
                    SET estado_local = 'pendiente'
                    WHERE tipo_cola = ? AND estado_local = 'en_proceso' 
                      AND (fecha_procesado IS NULL OR fecha_procesado < DATETIME('now', ?));
                """, (self.tipo_cola, f"-{minutos_inactividad} minutes"))
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error liberando huérfanos locales: {e}")
            return 0
        finally:
            conn.close()

    def obtener_estadisticas(self) -> Dict[str, int]:
        """Retorna el conteo de tareas locales agrupado por estado."""
        stats = {
            "pendiente": 0,
            "en_proceso": 0,
            "listo_para_subir": 0,
            "sincronizado": 0,
            "fallido": 0,
            "total": 0
        }
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT estado_local, COUNT(*) as cnt
                FROM tareas_staging
                WHERE tipo_cola = ?
                GROUP BY estado_local;
            """, (self.tipo_cola,))
            for row in cursor.fetchall():
                est = row["estado_local"]
                cnt = row["cnt"]
                stats[est] = cnt
                stats["total"] += cnt
            return stats
        except Exception as e:
            logger.error(f"Error obteniendo estadísticas SQLite: {e}")
            return stats
        finally:
            conn.close()

    # --- MÉTODOS AUXILIARES DE SINCRONIZACIÓN Y AGUA DE RESERVA (WATERMARK) ---

    def insertar_tareas_descargadas(self, tareas: List[Dict[str, Any]], tipo_cola: Optional[str] = None) -> int:
        """Inserta en bloque las tareas descargadas del VPS."""
        if not tareas:
            return 0
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            valores = []
            for t in tareas:
                id_vps = t["id"]
                linea = str(t.get("numero_de_linea") or t.get("ani") or "").strip()
                dni = str(t.get("dni") or "").strip() or None
                auto_id = str(t.get("auto_id") or self.auto_id)
                target_pc = str(t.get("target_pc") or self.pc_id)
                scraper_act = str(t.get("scraper_actual") or "telcos")
                payload_raw = t.get("datos") or t.get("datos_json") or t.get("payload") or {}
                if isinstance(payload_raw, (dict, list)):
                    payload_str = json.dumps(payload_raw, ensure_ascii=False)
                else:
                    payload_str = str(payload_raw) if payload_raw else "{}"

                valores.append((
                    id_vps, q_type, linea, dni, auto_id, target_pc, scraper_act, payload_str
                ))

            cursor = conn.executemany("""
                INSERT OR IGNORE INTO tareas_staging (
                    id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, scraper_actual, payload_origen, estado_local
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente');
            """, valores)
            conn.execute("COMMIT;")
            logger.info(f"💾 Inyectadas {len(valores)} tareas en SQLite local ({q_type})")
            return len(valores)
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error(f"Error insertando tareas en staging local: {e}")
            return 0
        finally:
            conn.close()

    def obtener_lote_para_push(self, limit: int = 5000, tipo_cola: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtiene un lote de registros terminados esperando subida nocturna."""
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, 
                       scraper_actual, resultado_json, fuente, scrapers_intentados, 
                       descripcion, latencia, error_msg, estado_local
                FROM tareas_staging
                WHERE estado_local IN ('listo_para_subir', 'fallido') AND tipo_cola = ?
                ORDER BY id_vps ASC
                LIMIT ?;
            """, (q_type, limit))
            filas = [dict(r) for r in cursor.fetchall()]
            return filas
        except Exception as e:
            logger.error(f"Error leyendo lote para push en SQLite: {e}")
            return []
        finally:
            conn.close()

    def marcar_como_sincronizados(self, ids_vps: List[int], tipo_cola: Optional[str] = None) -> bool:
        """Marca filas como 'sincronizado' registrando fecha_sincronizado = NOW()."""
        if not ids_vps:
            return True
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            placeholders = ",".join("?" for _ in ids_vps)
            with conn:
                conn.execute(f"""
                    UPDATE tareas_staging
                    SET estado_local = 'sincronizado',
                        fecha_sincronizado = CURRENT_TIMESTAMP
                    WHERE tipo_cola = ? AND id_vps IN ({placeholders});
                """, [q_type] + ids_vps)
            return True
        except Exception as e:
            logger.error(f"Error marcando tareas como sincronizadas: {e}")
            return False
        finally:
            conn.close()

    def purgar_antiguos(self, dias_retencion: int = 7) -> int:
        """Elimina de SQLite registros sincronizados con más de N días de antigüedad."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute("""
                    DELETE FROM tareas_staging
                    WHERE estado_local = 'sincronizado' 
                      AND fecha_sincronizado < DATETIME('now', ?);
                """, (f"-{dias_retencion} days",))
                filas_purgadas = cursor.rowcount
            if filas_purgadas > 0:
                logger.info(f"🧹 Purga rotativa SQLite: {filas_purgadas} registros eliminados (> {dias_retencion} días).")
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception:
                    pass
            return filas_purgadas
        except Exception as e:
            logger.error(f"Error en purga rotativa SQLite: {e}")
            return 0
        finally:
            conn.close()

    def contar_pendientes(self, tipo_cola: Optional[str] = None) -> int:
        """Devuelve la cantidad actual de registros 'pendiente' en SQLite local."""
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT COUNT(*) as pendientes
                FROM tareas_staging
                WHERE estado_local = 'pendiente' AND tipo_cola = ?;
            """, (q_type,))
            row = cursor.fetchone()
            return row["pendientes"] if row else 0
        except Exception as e:
            logger.error(f"Error contando pendientes locales: {e}")
            return 0
        finally:
            conn.close()
