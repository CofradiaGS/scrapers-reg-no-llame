# -*- coding: utf-8 -*-
"""
Módulo de Gestión y Tracking Centralizado de IPs para Scraper ENACOM Web.
Controla el consumo diario (100 consultas/día por IP) y el rate limit (5 consultas/minuto por IP)
mediante SQLite en modo WAL para garantizar concurrencia multiproceso ultra-rápida y libre de bloqueos.
"""
import os
import json
import sqlite3
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger("EnacomIPTracker")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "enacom_ip_registry.db"
JSON_BANNED_PATH = Path(__file__).resolve().parents[3] / "data" / "enacom_ips_bloqueadas.json"


class EnacomIPTracker:
    """
    Rastreador de estado y consumo de IPs concurrentes para ENACOM Web.
    Persiste en SQLite WAL para soportar 20+ workers simultáneos sin race conditions.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(EnacomIPTracker, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        """Inicializa la base de datos SQLite en modo WAL."""
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(DB_PATH), timeout=10.0) as conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA busy_timeout = 5000;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS enacom_ip_status (
                        ip TEXT PRIMARY KEY,
                        fecha_dia TEXT NOT NULL,
                        consultas_hoy INTEGER DEFAULT 0,
                        ultima_consulta REAL DEFAULT 0,
                        bloqueada_100 INTEGER DEFAULT 0,
                        cooldown_hasta REAL DEFAULT 0,
                        motivo_bloqueo TEXT,
                        actualizado_el TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_enacom_ip_fecha ON enacom_ip_status (fecha_dia, bloqueada_100);")
                conn.commit()
            
            # Sincronizar IPs pre-existentes en JSON si no están en SQLite
            self._sync_legacy_json()
        except Exception as e:
            logger.warning(f"Aviso inicializando SQLite IP Tracker ({e}).")

    def _sync_legacy_json(self):
        """Importa las IPs bloqueadas desde enacom_ips_bloqueadas.json histórico."""
        if not JSON_BANNED_PATH.exists():
            return
        try:
            with open(JSON_BANNED_PATH, "r", encoding="utf-8") as f:
                banned = json.load(f)
            hoy = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(str(DB_PATH), timeout=10.0) as conn:
                for ip, data in banned.items():
                    expira = data.get("expira", "")
                    if expira and expira > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
                        conn.execute("""
                            INSERT INTO enacom_ip_status (ip, fecha_dia, consultas_hoy, bloqueada_100, motivo_bloqueo, actualizado_el)
                            VALUES (?, ?, 100, 1, ?, datetime('now', 'localtime'))
                            ON CONFLICT(ip) DO UPDATE SET
                                bloqueada_100 = 1,
                                motivo_bloqueo = excluded.motivo_bloqueo;
                        """, (ip, hoy, data.get("motivo", "100 consultas diarias alcanzadas")))
                conn.commit()
        except Exception as e:
            logger.debug(f"Aviso sincronización JSON legacy: {e}")

    def evaluar_ip(self, ip: str) -> Tuple[bool, str, float]:
        """
        Evalúa si una IP asignada por el proxy es apta para consultar ENACOM Web.
        Retorna:
            (es_apta: bool, motivo: str, segundos_espera: float)
        """
        if not ip or ip in ("Desconocida", "Rotativa-Smartproxy"):
            return True, "IP no identificada; permitiendo consulta", 0.0

        hoy = datetime.now().strftime("%Y-%m-%d")
        now_ts = time.time()

        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM enacom_ip_status WHERE ip = ?", (ip,))
                row = cur.fetchone()

                if not row:
                    # IP nueva, 100% limpia
                    return True, "IP fresca", 0.0

                fecha_dia = row["fecha_dia"]
                bloqueada_100 = row["bloqueada_100"]
                consultas_hoy = row["consultas_hoy"]
                cooldown_hasta = row["cooldown_hasta"]
                ultima_consulta = row["ultima_consulta"]

                # Si es un nuevo día, resetear contadores diarios
                if fecha_dia != hoy:
                    cur.execute("""
                        UPDATE enacom_ip_status 
                        SET fecha_dia = ?, consultas_hoy = 0, bloqueada_100 = 0, cooldown_hasta = 0, actualizado_el = datetime('now', 'localtime')
                        WHERE ip = ?
                    """, (hoy, ip))
                    conn.commit()
                    return True, "IP reiniciada por nuevo día", 0.0

                # 1. Verificar si alcanzó las 100 consultas diarias
                if bloqueada_100 == 1:
                    return False, f"IP {ip} ya consumió las 100 consultas diarias en ENACOM", 0.0

                if consultas_hoy >= 95:
                    return False, f"IP {ip} en umbral de seguridad ({consultas_hoy}/100 consultas hoy)", 0.0

                # 2. Verificar si está en período de enfriamiento por límite de 5 consultas/minuto
                if cooldown_hasta and cooldown_hasta > now_ts:
                    restante = round(cooldown_hasta - now_ts, 1)
                    return False, f"IP {ip} en cooldown de 5/minuto (restan {restante}s)", restante

                # 3. Pacing multi-worker: evitar que 2 workers usen la misma IP en menos de 12 segundos
                if ultima_consulta and (now_ts - ultima_consulta) < 12.0:
                    espera_pacing = round(12.0 - (now_ts - ultima_consulta), 2)
                    return True, f"Pacing anti-rate limit ({espera_pacing}s)", espera_pacing

                return True, f"IP operativa ({consultas_hoy}/100 consultas hoy)", 0.0
        except Exception as e:
            logger.debug(f"Aviso evaluando IP {ip}: {e}")
            return True, "Error verificando IP; fallback permisivo", 0.0

    def registrar_consulta_exitosa(self, ip: str) -> None:
        """Registra una consulta exitosa realizada con la IP."""
        if not ip or ip in ("Desconocida", "Rotativa-Smartproxy"):
            return
        hoy = datetime.now().strftime("%Y-%m-%d")
        now_ts = time.time()
        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                conn.execute("""
                    INSERT INTO enacom_ip_status (ip, fecha_dia, consultas_hoy, ultima_consulta, actualizado_el)
                    VALUES (?, ?, 1, ?, datetime('now', 'localtime'))
                    ON CONFLICT(ip) DO UPDATE SET
                        fecha_dia = excluded.fecha_dia,
                        consultas_hoy = CASE WHEN enacom_ip_status.fecha_dia = excluded.fecha_dia THEN enacom_ip_status.consultas_hoy + 1 ELSE 1 END,
                        ultima_consulta = excluded.ultima_consulta,
                        bloqueada_100 = CASE WHEN enacom_ip_status.consultas_hoy + 1 >= 98 THEN 1 ELSE enacom_ip_status.bloqueada_100 END,
                        actualizado_el = datetime('now', 'localtime');
                """, (ip, hoy, now_ts))
                conn.commit()
        except Exception as e:
            logger.debug(f"Aviso registrando consulta en IP {ip}: {e}")

    def registrar_bloqueo_100(self, ip: str, motivo: str = "Ha alcanzado el límite de 100 consultas diarias.") -> None:
        """Marca la IP como agotada (100 consultas diarias) hasta la medianoche."""
        if not ip or ip in ("Desconocida", "Rotativa-Smartproxy"):
            return
        hoy = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                conn.execute("""
                    INSERT INTO enacom_ip_status (ip, fecha_dia, consultas_hoy, bloqueada_100, motivo_bloqueo, actualizado_el)
                    VALUES (?, ?, 100, 1, ?, datetime('now', 'localtime'))
                    ON CONFLICT(ip) DO UPDATE SET
                        fecha_dia = excluded.fecha_dia,
                        bloqueada_100 = 1,
                        consultas_hoy = 100,
                        motivo_bloqueo = excluded.motivo_bloqueo,
                        actualizado_el = datetime('now', 'localtime');
                """, (ip, hoy, motivo))
                conn.commit()

            # Escribir también a JSON para backward compatibility de forma segura
            self._actualizar_json_bloqueadas(ip, motivo)
            logger.info(f"🚫 [IP TRACKER] IP {ip} registrada como BLOQUEADA 100/DÍA: {motivo}")
        except Exception as e:
            logger.debug(f"Aviso registrando bloqueo 100 en IP {ip}: {e}")

    def registrar_rate_limit_minuto(self, ip: str, segundos: float = 65.0, motivo: str = "Ha superado el límite de 5 consultas por minuto.") -> None:
        """Pone la IP en enfriamiento temporal para evitar quemar consultas y ancho de banda."""
        if not ip or ip in ("Desconocida", "Rotativa-Smartproxy"):
            return
        hoy = datetime.now().strftime("%Y-%m-%d")
        cooldown_ts = time.time() + segundos
        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                conn.execute("""
                    INSERT INTO enacom_ip_status (ip, fecha_dia, cooldown_hasta, motivo_bloqueo, actualizado_el)
                    VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                    ON CONFLICT(ip) DO UPDATE SET
                        fecha_dia = excluded.fecha_dia,
                        cooldown_hasta = excluded.cooldown_hasta,
                        motivo_bloqueo = excluded.motivo_bloqueo,
                        actualizado_el = datetime('now', 'localtime');
                """, (ip, hoy, cooldown_ts, motivo))
                conn.commit()
            logger.info(f"⏳ [IP TRACKER] IP {ip} puesta en enfriamiento ({segundos:.0f}s) por límite 5/min: {motivo}")
        except Exception as e:
            logger.debug(f"Aviso registrando rate limit 5/min en IP {ip}: {e}")

    def _actualizar_json_bloqueadas(self, ip: str, motivo: str):
        """Actualiza el archivo JSON de IPs bloqueadas protegiendo concurrencia."""
        try:
            ahora = datetime.now()
            expira = ahora.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
            banned = {}
            if JSON_BANNED_PATH.exists():
                try:
                    with open(JSON_BANNED_PATH, "r", encoding="utf-8") as f:
                        banned = json.load(f)
                except Exception:
                    banned = {}
            banned[ip] = {
                "motivo": motivo,
                "bloqueado_el": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                "expira": expira
            }
            tmp_path = JSON_BANNED_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(banned, f, indent=2)
            os.replace(tmp_path, JSON_BANNED_PATH)
        except Exception as e:
            logger.debug(f"Aviso guardando JSON bloqueadas: {e}")

    def obtener_estadisticas_hoy(self) -> Dict[str, Any]:
        """Devuelve métricas consolidadas del pool de IPs para monitoreo e informes."""
        hoy = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM enacom_ip_status WHERE fecha_dia = ?", (hoy,))
                total_ips = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM enacom_ip_status WHERE fecha_dia = ? AND bloqueada_100 = 1", (hoy,))
                bloqueadas = cur.fetchone()[0]

                cur.execute("SELECT SUM(consultas_hoy) FROM enacom_ip_status WHERE fecha_dia = ?", (hoy,))
                res_sum = cur.fetchone()[0]
                total_consultas = res_sum if res_sum is not None else 0

                cur.execute("SELECT COUNT(*) FROM enacom_ip_status WHERE fecha_dia = ? AND cooldown_hasta > ?", (hoy, time.time()))
                en_cooldown = cur.fetchone()[0]

                return {
                    "fecha": hoy,
                    "total_ips_usadas": total_ips,
                    "bloqueadas_100_hoy": bloqueadas,
                    "en_cooldown_minuto": en_cooldown,
                    "total_consultas_exitosas": total_consultas,
                }
        except Exception:
            return {"fecha": hoy, "total_ips_usadas": 0, "bloqueadas_100_hoy": 0, "total_consultas_exitosas": 0}
