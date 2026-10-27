# -*- coding: utf-8 -*-
"""
Adaptador de Red: Gestor de Lista de Descanso y Cooldown de IPs
Controla y aísla de manera multiproceso (SQLite WAL compartido entre workers)
aquellas direcciones IP que recibieron 2 bloqueos o captchas consecutivos en Datuar.
Las coloca en cuarentena temporal hasta que vuelvan a estar 'potables' (default 30 min).
"""
import os
import time
import sqlite3
import logging
from contextlib import contextmanager
from typing import Tuple, List, Optional

logger = logging.getLogger("IPCooldownManager")


class IPCooldownManager:
    """
    Administrador multiproceso de lista de descanso de IPs con strikes.
    Thread-safe y Process-safe garantizado mediante transacciones atómicas SQLite.
    """
    _instance: Optional["IPCooldownManager"] = None

    _cached_descanso_ips: set = set()
    _cached_descanso_time: float = 0.0

    def __init__(self, db_path: Optional[str] = None, default_cooldown_sec: float = 900.0):
        if db_path is None:
            base_dir = os.path.join(os.getcwd(), "tor_data")
            os.makedirs(base_dir, exist_ok=True)
            db_path = os.path.join(base_dir, "ip_cooldown.db")
        self.db_path = os.path.abspath(db_path)
        self.default_cooldown_sec = float(default_cooldown_sec)
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None, default_cooldown_sec: float = 900.0) -> "IPCooldownManager":
        if cls._instance is None:
            cls._instance = cls(db_path=db_path, default_cooldown_sec=default_cooldown_sec)
        return cls._instance

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Crea la tabla de control de IPs si no existe."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ip_strikes (
                    ip TEXT PRIMARY KEY,
                    strikes INTEGER DEFAULT 0,
                    ultimo_bloqueo REAL DEFAULT 0,
                    cooldown_hasta REAL DEFAULT 0,
                    en_descanso INTEGER DEFAULT 0,
                    total_cooldowns INTEGER DEFAULT 0
                );
            """)
            conn.commit()

    def registrar_bloqueo(self, ip: str, cooldown_segundos: Optional[float] = None) -> Tuple[int, bool]:
        """
        Registra un evento de captcha / bloqueo para una IP.
        Si la IP acumula 2 strikes, se coloca de inmediato en 'lista de descanso'.
        Retorna: (strikes_actuales, es_nuevo_ingreso_a_descanso)
        """
        if not ip or not isinstance(ip, str):
            return 0, False

        ip_clean = ip.strip()
        now = time.time()
        cd_sec = float(cooldown_segundos if cooldown_segundos is not None else self.default_cooldown_sec)

        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT strikes, cooldown_hasta, en_descanso FROM ip_strikes WHERE ip = ?", (ip_clean,))
            row = cur.fetchone()

            if row:
                strikes, cooldown_hasta, en_descanso = row
                # Si ya estaba en descanso pero el tiempo expiró, resetear ciclo
                if en_descanso == 1 and now >= cooldown_hasta:
                    strikes = 1
                    en_descanso = 0
                    cooldown_hasta = 0
                else:
                    strikes += 1

                if strikes >= 2:
                    ya_estaba = (en_descanso == 1 and now < cooldown_hasta)
                    en_descanso = 1
                    cooldown_hasta = now + cd_sec
                    cur.execute("""
                        UPDATE ip_strikes 
                        SET strikes = ?, ultimo_bloqueo = ?, cooldown_hasta = ?, en_descanso = 1, total_cooldowns = total_cooldowns + 1
                        WHERE ip = ?
                    """, (strikes, now, cooldown_hasta, ip_clean))
                    conn.commit()
                    self._cached_descanso_dict[ip_clean] = cooldown_hasta
                    self._cached_descanso_time = now
                    return strikes, (not ya_estaba)
                else:
                    cur.execute("""
                        UPDATE ip_strikes 
                        SET strikes = ?, ultimo_bloqueo = ?
                        WHERE ip = ?
                    """, (strikes, now, ip_clean))
                    conn.commit()
                    return strikes, False
            else:
                # Primer strike para esta IP
                cur.execute("""
                    INSERT INTO ip_strikes (ip, strikes, ultimo_bloqueo, cooldown_hasta, en_descanso, total_cooldowns)
                    VALUES (?, 1, ?, 0, 0, 0)
                """, (ip_clean, now))
                conn.commit()
                return 1, False

    _cached_descanso_dict: dict = {}
    _cached_descanso_time: float = 0.0

    def esta_en_descanso(self, ip: str) -> bool:
        """
        Comprueba de forma ultra-rápida en RAM si la IP está en descanso.
        Evalúa el timestamp de expiración exacto para liberar la IP tan pronto como esté potable.
        """
        if not ip:
            return False
        ip_clean = ip.strip()
        now = time.time()

        cd_hasta = getattr(self, "_cached_descanso_dict", {}).get(ip_clean)
        if cd_hasta is not None and now < cd_hasta:
            return True

        if now - getattr(self, "_cached_descanso_time", 0.0) >= 2.0 or cd_hasta is not None:
            try:
                with self._connection() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE ip_strikes SET en_descanso = 0, strikes = 0 WHERE en_descanso = 1 AND cooldown_hasta <= ?", (now,))
                    conn.commit()
                    cur.execute("SELECT ip, cooldown_hasta FROM ip_strikes WHERE en_descanso = 1 AND cooldown_hasta > ?", (now,))
                    self._cached_descanso_dict = dict(cur.fetchall())
                    self._cached_descanso_time = now
            except Exception:
                pass

        cd_hasta = getattr(self, "_cached_descanso_dict", {}).get(ip_clean)
        return bool(cd_hasta and now < cd_hasta)

    def reportar_exito(self, ip: str) -> None:
        """Si la IP responde limpiamente sin captcha ni bloqueo, resetea sus strikes acumulados."""
        if not ip:
            return
        ip_clean = ip.strip()
        with self._connection() as conn:
            conn.execute("UPDATE ip_strikes SET strikes = 0 WHERE ip = ? AND en_descanso = 0", (ip_clean,))
            conn.commit()

    def obtener_ips_en_descanso(self) -> List[str]:
        """Devuelve la lista activa de todas las IPs que actualmente están en período de descanso."""
        now = time.time()
        with self._connection() as conn:
            cur = conn.cursor()
            # Limpiar expiradas primero
            cur.execute("UPDATE ip_strikes SET en_descanso = 0, strikes = 0 WHERE en_descanso = 1 AND cooldown_hasta <= ?", (now,))
            conn.commit()

            cur.execute("SELECT ip FROM ip_strikes WHERE en_descanso = 1 AND cooldown_hasta > ?", (now,))
            return [r[0] for r in cur.fetchall()]

    def limpiar_todo(self) -> None:
        """Resetea completamente la base de datos de cooldowns."""
        with self._connection() as conn:
            conn.execute("DELETE FROM ip_strikes")
            conn.commit()
