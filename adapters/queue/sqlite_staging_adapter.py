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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_tipo_estado_scraper ON tareas_staging(tipo_cola, estado_local, scraper_actual);")
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
        asignadas al scraper especificado y las pasa de inmediato a 'en_proceso'.
        """
        registros: List[RegistroCola] = []
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")

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
            elif "bcra" in nombre_clean:
                canon = "bcra"
            elif "enacom" in nombre_clean:
                canon = "enacom_web"
            else:
                canon = nombre_clean


            if canon == "telcos":
                cursor = conn.execute("""
                    SELECT id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, 
                           scraper_actual, payload_origen, resultado_json, fuente
                    FROM tareas_staging
                    WHERE estado_local = 'pendiente' AND tipo_cola = ?
                      AND scraper_actual IN ('telcos', 'claro', 'personal', 'movistar')
                    ORDER BY (dni IS NOT NULL AND dni != '') DESC, id_vps ASC
                    LIMIT ?;
                """, (self.tipo_cola, batch_size))
            else:
                cursor = conn.execute("""
                    SELECT id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, 
                           scraper_actual, payload_origen, resultado_json, fuente
                    FROM tareas_staging
                    WHERE estado_local = 'pendiente' AND tipo_cola = ?
                      AND scraper_actual = ?
                    ORDER BY (dni IS NOT NULL AND dni != '') DESC, id_vps ASC
                    LIMIT ?;
                """, (self.tipo_cola, canon, batch_size))
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
                raw_payload = f["resultado_json"] or f["payload_origen"]
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
                    fuente=f["fuente"],
                    datos_existentes=payload_dict,
                    tipo_cola=f["tipo_cola"]
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

                # Extraer DNI resultante si no vino explícito en el ítem
                if not dni_item and isinstance(datos_totales, dict):
                    from core.domain.entities import ReglaPipeline
                    dni_ext = ReglaPipeline.extraer_dni(datos_totales)
                    if dni_ext:
                        dni_item = str(dni_ext).strip()

                # Extraer lista de scrapers ejecutados para scrapers_intentados
                intentados_lista = []
                if isinstance(datos_totales, dict):
                    for telco in ("claro", "personal", "movistar", "iris", "datuar", "cuitonline", "bcra", "enacom_web"):
                        if telco in datos_totales:
                            intentados_lista.append(telco)
                scrapers_intentados = ",".join(intentados_lista) if intentados_lista else scraper_actual

                is_finished = (
                    scraper_actual == "finalizado"
                    or str(item.get("estado") or "").lower() in ("completado", "completed")
                    or not scraper_actual
                )

                if status_raw in ("error", "failed", "fallido"):
                    estado_local = "fallido"
                    error_msg = desc or "Error en procesamiento local"
                elif is_finished:
                    estado_local = "listo_para_subir"
                    error_msg = None
                else:
                    # Posta intermedia del pipeline local (ej: iris -> telcos -> cuitonline/datuar -> bcra).
                    # Permanece como 'pendiente' en SQLite para que el siguiente scraper lo tome de inmediato
                    # sin esperar al ciclo nocturno ni generar round-trips al VPS.
                    estado_local = "pendiente"
                    error_msg = None

                valores.append((
                    estado_local,
                    res_json_str,
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
                    payload_origen = ?,
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

    def revertir_subida_a_listo(self, ids_vps: List[int], tipo_cola: Optional[str] = None) -> bool:
        """Devuelve tareas de 'en_subida' al estado 'listo_para_subir' si el push falló o se interrumpió."""
        if not ids_vps:
            return True
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            placeholders = ",".join("?" for _ in ids_vps)
            with conn:
                conn.execute(f"""
                    UPDATE tareas_staging
                    SET estado_local = 'listo_para_subir'
                    WHERE tipo_cola = ? AND estado_local = 'en_subida' AND id_vps IN ({placeholders});
                """, [q_type] + ids_vps)
            return True
        except Exception as e:
            logger.error(f"Error al revertir de en_subida a listo_para_subir en SQLite: {e}")
            return False
        finally:
            conn.close()

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        """Recupera registros colgados en 'en_proceso' o 'en_subida' tras caídas imprevistas o reinicios."""
        conn = self._get_connection()
        try:
            with conn:
                cursor1 = conn.execute("""
                    UPDATE tareas_staging
                    SET estado_local = 'pendiente'
                    WHERE tipo_cola = ? AND estado_local = 'en_proceso' 
                      AND (fecha_procesado IS NULL OR fecha_procesado < DATETIME('now', ?));
                """, (self.tipo_cola, f"-{minutos_inactividad} minutes"))
                h_proc = cursor1.rowcount

                cursor2 = conn.execute("""
                    UPDATE tareas_staging
                    SET estado_local = 'listo_para_subir'
                    WHERE tipo_cola = ? AND estado_local = 'en_subida' 
                      AND (fecha_procesado IS NULL OR fecha_procesado < DATETIME('now', ?));
                """, (self.tipo_cola, f"-{minutos_inactividad} minutes"))
                h_subida = cursor2.rowcount
                return h_proc + h_subida
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
            "en_subida": 0,
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

                fuente_raw = t.get("fuente")
                if isinstance(fuente_raw, list):
                    fuente_str = json.dumps(fuente_raw, ensure_ascii=False)
                elif fuente_raw is not None:
                    fuente_str = str(fuente_raw)
                else:
                    fuente_str = None

                valores.append((
                    id_vps, q_type, linea, dni, auto_id, target_pc, scraper_act, payload_str, fuente_str
                ))

            cursor = conn.executemany("""
                INSERT INTO tareas_staging (
                    id_vps, tipo_cola, numero_de_linea, dni, auto_id, target_pc, scraper_actual, payload_origen, fuente, estado_local
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente')
                ON CONFLICT(id_vps, tipo_cola) DO NOTHING;
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
        """Obtiene un lote de registros terminados esperando subida nocturna y los reserva atómicamente como 'en_subida'."""
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
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
            if filas:
                ids_vps = [f["id_vps"] for f in filas]
                placeholders = ",".join("?" for _ in ids_vps)
                conn.execute(f"""
                    UPDATE tareas_staging
                    SET estado_local = 'en_subida'
                    WHERE tipo_cola = ? AND id_vps IN ({placeholders});
                """, [q_type] + ids_vps)
                for f in filas:
                    f["estado_local"] = "en_subida"
            conn.execute("COMMIT;")
            return filas
        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
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

    def contar_pendientes(self, tipo_cola: Optional[str] = None, scraper_actual: Optional[str] = None) -> int:
        """Devuelve la cantidad actual de registros 'pendiente' en SQLite local, opcionalmente por scraper_actual."""
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            if scraper_actual:
                nombre_clean = scraper_actual.lower().strip()
                if "enacom" in nombre_clean:
                    canon = "enacom_web"
                elif "iris" in nombre_clean:
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
                elif "bcra" in nombre_clean:
                    canon = "bcra"
                elif "telco" in nombre_clean:
                    canon = "telcos"
                else:
                    canon = nombre_clean

                if canon == "telcos":
                    cursor = conn.execute("""
                        SELECT COUNT(*) as pendientes
                        FROM tareas_staging
                        WHERE estado_local = 'pendiente' AND tipo_cola = ? 
                          AND scraper_actual IN ('telcos', 'claro', 'personal', 'movistar');
                    """, (q_type,))
                else:
                    cursor = conn.execute("""
                        SELECT COUNT(*) as pendientes
                        FROM tareas_staging
                        WHERE estado_local = 'pendiente' AND tipo_cola = ? AND scraper_actual = ?;
                    """, (q_type, canon))
            else:
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

    def contar_listos_para_subir(self, tipo_cola: Optional[str] = None) -> int:
        """Devuelve la cantidad actual de registros listos para subir ('listo_para_subir' o 'fallido') en SQLite local."""
        q_type = tipo_cola or self.tipo_cola
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT COUNT(*) as listos
                FROM tareas_staging
                WHERE estado_local IN ('listo_para_subir', 'fallido') AND tipo_cola = ?;
            """, (q_type,))
            row = cursor.fetchone()
            return row["listos"] if row else 0
        except Exception as e:
            logger.error(f"Error contando registros listos para subir: {e}")
            return 0
        finally:
            conn.close()

