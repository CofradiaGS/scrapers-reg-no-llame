# -*- coding: utf-8 -*-
"""
Runtime: Supervisor Industrial 24/7 de Procesos
Controlador maestro autónomo, tolerante a fallas y auto-regenerativo.
Gestiona el ciclo de vida de un pool de workers concurrentes bajo arquitectura hexagonal:
- Mantiene N workers concurrentes con auto-reemplazo continuo.
- Monitorea la salud de la red/VPN mediante Circuit Breaker.
- Ejecuta el caso de uso LiberarHuerfanosUseCase mediante Watchdog Sweeper.
- Presenta métricas industriales en tiempo real mediante Dashboard Thread.
- Apagado ordenado ante señales SIGINT / SIGTERM protegiendo la integridad de la base de datos.
"""
import os
import sys
import time
import signal
import urllib.request
import logging
from logging.handlers import RotatingFileHandler
import threading
from typing import Dict, Any, Optional
from multiprocessing import Process, Queue, Event
from pathlib import Path

import config
from core.domain.schedule import PoliticaHorarioComercial
from core.use_cases.cleanup_orphans_use_case import LiberarHuerfanosUseCase
from core.ports.queue_port import IColaRepositorioPort
from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
from adapters.queue.cola_automatizacion_adapter import ColaAutomatizacionAdapter
from runtime.worker_process import worker_lifecycle_process

logger = logging.getLogger("SupervisorIndustrial")

class SupervisorIndustrial:
    """Orquestador industrial de procesos workers y centinelas 24/7."""

    def __init__(
        self,
        scraper_name: str = "iris",
        workers: int = 9,
        batch_size: int = 50,
        max_queries_worker: int = 350,
        prioridad: Optional[int] = None,
        delay_min: float = 1.5,
        delay_max: float = 2.5,
        scraper_kwargs: Optional[Dict[str, Any]] = None,
        verificar_vpn: bool = True,
        minutos_inactividad_huerfanos: int = 15,
        forzar_horario: bool = False,
        solo_sin_coincidencia: bool = False,
        queue_type: str = "registro_no_llame",
        auto_id: str = "iris_scraper",
        pc_id: Optional[str] = None,
        buffer_flush_size: Optional[int] = None,
        buffer_max_delay: Optional[float] = None,
        heartbeat_interval: Optional[float] = None,
        stats_flush_interval: Optional[float] = None,
        watchdog_sweep_interval: Optional[float] = None,
        empty_queue_pause_max_sec: Optional[float] = None
    ):
        self.scraper_name = scraper_name
        self.queue_type = (queue_type or getattr(config, "QUEUE_TYPE", "registro_no_llame")).lower()
        self.auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "iris_scraper")
        self.pc_id = pc_id or getattr(config, "WORKER_PC_ID", None)
        self.workers_count = workers
        self.batch_size = batch_size
        self.max_queries_worker = max_queries_worker
        self.prioridad = prioridad
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.scraper_kwargs = scraper_kwargs or {}
        self.verificar_vpn = verificar_vpn
        self.minutos_inactividad_huerfanos = minutos_inactividad_huerfanos
        self.forzar_horario = forzar_horario
        self.solo_sin_coincidencia = solo_sin_coincidencia

        # Asegurar que el flag forzar_horario se propague al motor del scraper
        if "forzar_horario" not in self.scraper_kwargs:
            self.scraper_kwargs["forzar_horario"] = self.forzar_horario

        self.politica_horario = PoliticaHorarioComercial(
            activo=config.HORARIO_COMERCIAL_ACTIVO,
            hora_inicio_lv=config.HORARIO_COMERCIAL_INICIO_LV,
            hora_fin_lv=config.HORARIO_COMERCIAL_FIN_LV,
            hora_inicio_sab=config.HORARIO_COMERCIAL_INICIO_SAB,
            hora_fin_sab=config.HORARIO_COMERCIAL_FIN_SAB,
            timezone_name=config.HORARIO_COMERCIAL_TIMEZONE
        )

        self.stop_event = Event()
        self.pause_event = Event()
        self._pause_reasons: set[str] = set()
        self._pause_lock = threading.Lock()
        self.stats_queue = Queue()
        self.worker_slots: Dict[int, Dict[str, Any]] = {}

        # Canales de comunicación IPC (Despachador centralizado de base de datos)
        self.db_request_queue = Queue()
        self.slot_response_queues: Dict[int, Queue] = {}
        self.db_adapter: Optional[IColaRepositorioPort] = None
        self._dispatcher_stop = threading.Event()

        # In-Memory Buffer de Persistencia Masiva (reducción drástica de binlog)
        self._write_buffer: List[Dict[str, Any]] = []
        self._last_buffer_flush: float = time.time()
        self.buffer_flush_size: int = (
            buffer_flush_size
            if buffer_flush_size is not None
            else getattr(config, "BUFFER_FLUSH_SIZE", 500)
        )
        self.buffer_max_delay: float = (
            buffer_max_delay
            if buffer_max_delay is not None
            else getattr(config, "BUFFER_MAX_DELAY", 300.0)
        )
        self.heartbeat_interval: float = (
            heartbeat_interval
            if heartbeat_interval is not None
            else getattr(config, "HEARTBEAT_INTERVAL_SEC", 180.0)
        )
        self.stats_flush_interval: float = (
            stats_flush_interval
            if stats_flush_interval is not None
            else getattr(config, "STATS_FLUSH_INTERVAL_SEC", 600.0)
        )
        self.watchdog_sweep_interval: float = (
            watchdog_sweep_interval
            if watchdog_sweep_interval is not None
            else getattr(config, "WATCHDOG_SWEEP_INTERVAL_SEC", 600.0)
        )
        self._last_heartbeat_time: float = 0.0
        self._last_stats_flush_time: float = time.time()
        self._pending_stats: Dict[str, int] = {"procesados": 0, "enriquecidos": 0, "fallidos": 0}

        # Centinela Explorador (Scout Probe) y Backoff Progresivo para cola vacía
        self.empty_queue_pause_max_sec: float = max(
            30.0,
            empty_queue_pause_max_sec
            if empty_queue_pause_max_sec is not None
            else getattr(config, "EMPTY_QUEUE_PAUSE_MAX_SEC", 900.0)
        )
        self._empty_backoff_level: int = 0
        self._scout_prefetched_lote: List[Any] = []
        self._scout_lock = threading.Lock()

    def _get_current_empty_backoff_delay(self) -> float:
        """Calcula el retardo actual de la escalera de backoff progresivo para cola vacía."""
        ladder = [60.0, 180.0, 300.0, 600.0, self.empty_queue_pause_max_sec]
        idx = min(self._empty_backoff_level, len(ladder) - 1)
        return min(ladder[idx], self.empty_queue_pause_max_sec)

    def _set_pause(self, razon: str):
        """Activa una causa de pausa de forma atómica y segura entre hilos."""
        with self._pause_lock:
            self._pause_reasons.add(razon)
            self.pause_event.set()

    def _clear_pause(self, razon: str):
        """Desactiva una causa de pausa. Solo despausa workers si no restan otras causas."""
        with self._pause_lock:
            self._pause_reasons.discard(razon)
            if not self._pause_reasons:
                self.pause_event.clear()

    def _obtener_estado_pausa(self) -> str:
        """Devuelve un resumen legible del estado operativo para el dashboard."""
        with self._pause_lock:
            if "cola_vacia" in self._pause_reasons:
                delay = self._get_current_empty_backoff_delay()
                return f"PAUSADO (Cola Vacía - Centinela esperando {int(delay)}s | Nivel {self._empty_backoff_level + 1})"
            if "horario" in self._pause_reasons and "vpn" in self._pause_reasons:
                return "PAUSADO (Fuera de Horario Comercial + Red/VPN Caída)"
            if "horario" in self._pause_reasons:
                estado_h = self.politica_horario.obtener_estado()
                return f"PAUSADO ({estado_h['descripcion']})"
            if "vpn" in self._pause_reasons:
                return "PAUSADO (Red/VPN Caída)"
            if self.pause_event.is_set():
                return "PAUSADO"
            if "iris" in self.scraper_name and not self.forzar_horario:
                estado_h = self.politica_horario.obtener_estado()
                return f"VERDE ({estado_h['descripcion']})"
            return "VERDE (Operativo)"

    def _circuit_breaker_loop(self, check_interval: float = 25.0):
        """Monitor centinela de conectividad y VPN."""
        cb_logger = logging.getLogger("CircuitBreaker")
        fallos = 0
        test_url = config.IRIS_LOGIN_URL

        while not self.stop_event.is_set():
            # Suspender pings HTTP a Movistar si estamos fuera de horario comercial
            # para evitar saturar el portal en ventanas de mantenimiento y prevenir falsos positivos
            if "horario" in self._pause_reasons:
                for _ in range(5):
                    if self.stop_event.is_set() or "horario" not in self._pause_reasons:
                        break
                    time.sleep(1.0)
                continue

            if self.verificar_vpn and "iris" in self.scraper_name:
                try:
                    req = urllib.request.Request(
                        test_url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            if fallos >= 3:
                                cb_logger.info("🟢 [CIRCUIT BREAKER] Conectividad restablecida.")
                                self._clear_pause("vpn")
                            fallos = 0
                        else:
                            fallos += 1
                except Exception as e:
                    fallos += 1
                    if fallos == 1 or fallos % 5 == 0:
                        cb_logger.warning(f"⚠️ [CIRCUIT BREAKER] Fallo de conectividad ({fallos}/3): {e}")
                    if fallos >= 3:
                        cb_logger.error("🔴 [CIRCUIT BREAKER] Caída de red/VPN detectada. Pausando reclamo de lotes.")
                        self._set_pause("vpn")

            for _ in range(int(check_interval)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

    def _horario_comercial_loop(self):
        """
        Monitor centinela determinista de horario comercial para scrapers oficiales (IRIS Movistar).
        Calcula con 1 sola evaluación los segundos exactos restantes hasta la próxima apertura
        y duerme de forma interrumpible, eliminando el sondeo continuo (busy-waiting) y las consultas redundantes.
        """
        hc_logger = logging.getLogger("HorarioComercial")

        if self.forzar_horario or not ("iris" in self.scraper_name):
            return

        while not self.stop_event.is_set():
            estado = self.politica_horario.obtener_estado()
            esta_abierto = estado["abierto"]

            if not esta_abierto:
                segundos_espera = self.politica_horario.segundos_hasta_proxima_apertura()
                prox_dt = self.politica_horario.proxima_apertura()
                prox_str = prox_dt.strftime("%Y-%m-%d %H:%M:%S") if prox_dt else "N/A"
                horas = int(segundos_espera // 3600)
                minutos = int((segundos_espera % 3600) // 60)
                seg_rem = int(segundos_espera % 60)

                self._set_pause("horario")
                hc_logger.warning(
                    f"⏸️ [HORARIO COMERCIAL] Fuera de ventana operativa ({estado['descripcion']}).\n"
                    f"     • Próxima reapertura: {prox_str}\n"
                    f"     • Tiempo de reposo programado: {horas}h {minutos}m {seg_rem}s ({int(segundos_espera):,}s)\n"
                    f"     • Workers en reposo pasivo silencioso. Suspensión de sondeos HTTP nocturnos."
                )

                # Dormir de forma interrumpible hasta el instante exacto de reapertura
                t_despertar = time.time() + segundos_espera
                while time.time() < t_despertar and not self.stop_event.is_set():
                    tiempo_bloque = min(30.0, max(0.5, t_despertar - time.time()))
                    if self.stop_event.wait(timeout=tiempo_bloque):
                        break

                if self.stop_event.is_set():
                    break

                # Al expirar el tiempo de reposo, verificar estado de apertura
                estado_nuevo = self.politica_horario.obtener_estado()
                if estado_nuevo["abierto"]:
                    hc_logger.info(
                        f"🟢 [HORARIO COMERCIAL] Apertura comercial detectada: {estado_nuevo['descripcion']}. "
                        "Reanudando workers escalonadamente..."
                    )
                    self._clear_pause("horario")
            else:
                if "horario" in self._pause_reasons:
                    self._clear_pause("horario")

                # Durante horario comercial abierto, verificar cierre cada 30 segundos
                for _ in range(30):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1.0)

    def _flush_write_buffer(self, disp_logger=None) -> bool:
        """Vuelca en bloque todos los resultados acumulados en RAM en 1 sola transacción atómica a MySQL."""
        if not self._write_buffer:
            return True
        items_to_persist = list(self._write_buffer)
        self._write_buffer.clear()
        self._last_buffer_flush = time.time()
        try:
            ok = self.db_adapter.persistir_resultados(items_to_persist)
            if ok:
                if disp_logger:
                    disp_logger.info(
                        f"💾 [FLUSH BUFFER] Persistidos masivamente {len(items_to_persist)} "
                        f"registros en 1 sola transacción atómica a MySQL. (Próximo flush en {int(self.buffer_max_delay)}s o al llegar a {self.buffer_flush_size} reg)"
                    )
                return True
            else:
                # Re-encolar registros no persistidos para evitar pérdida de datos en RAM
                self._write_buffer = items_to_persist + self._write_buffer
                if disp_logger:
                    disp_logger.warning(
                        f"⚠️ [FLUSH BUFFER] Falló la persistencia atómica ({len(items_to_persist)} reg). "
                        "Re-encolados en RAM para reintento automático."
                    )
                return False
        except Exception as e:
            # Re-encolar ante excepción no controlada
            self._write_buffer = items_to_persist + self._write_buffer
            if disp_logger:
                disp_logger.error(
                    f"❌ [FLUSH BUFFER] Excepción al vaciar buffer de persistencia: {e}. "
                    f"Registros retenidos en RAM ({len(self._write_buffer)} en buffer)."
                )
            return False

    def _db_dispatcher_loop(self):
        """
        Hilo despachador centralizado de base de datos con Buffer en Memoria (RAM).
        - Desacopla la persistencia de los workers acumulando resultados en memoria.
        - Descarga a MySQL en bloques consolidados (default: 500 registros o máx 300s/5min).
        - Throttling de heartbeat (default: cada 180s) y stats (default: cada 600s) sin SELECT COUNT(*).
        - Vaciado atómico garantizado en bloque finally ante apagado o desconexión.
        """
        disp_logger = logging.getLogger("DBDispatcher")
        disp_logger.info(
            f"📡 [DB DISPATCHER] Hilo despachador IPC con Buffer en RAM iniciado "
            f"(Umbral: {self.buffer_flush_size} reg / {int(self.buffer_max_delay)}s | 1 conexión permanente a VPS)."
        )

        try:
            while not self._dispatcher_stop.is_set():
                msg = None
                try:
                    msg = self.db_request_queue.get(timeout=1.0)
                except Exception:
                    pass

                now = time.time()

                # 1. Flush por límite de tiempo si hay registros esperando en buffer (Max Latency)
                if self._write_buffer and (now - self._last_buffer_flush >= self.buffer_max_delay):
                    self._flush_write_buffer(disp_logger)

                # 2. Heartbeat espaciado a MySQL
                if now - self._last_heartbeat_time >= self.heartbeat_interval:
                    if hasattr(self.db_adapter, "enviar_heartbeat"):
                        self.db_adapter.enviar_heartbeat()
                    self._last_heartbeat_time = now

                # 3. Stats históricas acumuladas espaciadas sin COUNT(*)
                if now - self._last_stats_flush_time >= self.stats_flush_interval:
                    if hasattr(self.db_adapter, "guardar_session_stats"):
                        if self._pending_stats["procesados"] > 0 or self._pending_stats["fallidos"] > 0:
                            self.db_adapter.guardar_session_stats(
                                self._pending_stats["procesados"],
                                self._pending_stats["enriquecidos"],
                                self._pending_stats["fallidos"]
                            )
                            self._pending_stats = {"procesados": 0, "enriquecidos": 0, "fallidos": 0}
                    self._last_stats_flush_time = now

                if not msg:
                    continue

                action = msg.get("action")
                slot_id = msg.get("slot_id")
                resp_q = self.slot_response_queues.get(slot_id)

                try:
                    if action == "reservar_lote":
                        # 1. Si la cola está pausada por 'cola_vacia' o el supervisor se está deteniendo, responder vacío de inmediato
                        if "cola_vacia" in self._pause_reasons or self.stop_event.is_set():
                            if resp_q:
                                resp_q.put([])
                            continue

                        # 2. Handover de pre-fetch del Centinela Explorador (si encontró lote previamente)
                        with self._scout_lock:
                            if self._scout_prefetched_lote:
                                lote = self._scout_prefetched_lote
                                self._scout_prefetched_lote = []
                                disp_logger.info(
                                    f"📦 [DB DISPATCHER] Entregando lote pre-reservado por Centinela ({len(lote)} reg) al Slot {slot_id}."
                                )
                                if resp_q:
                                    resp_q.put(lote)
                                continue

                        # 3. Consulta a la base de datos
                        b_size = msg.get("batch_size", self.batch_size)
                        prio = msg.get("prioridad", self.prioridad)
                        s_nom = msg.get("scraper_nombre", self.scraper_name)
                        s_sin = msg.get("solo_sin_coincidencia", self.solo_sin_coincidencia)
                        lote = self.db_adapter.reservar_lote(
                            batch_size=b_size,
                            prioridad=prio,
                            scraper_nombre=s_nom,
                            solo_sin_coincidencia=s_sin
                        )

                        if not lote or len(lote) == 0:
                            if "cola_vacia" not in self._pause_reasons:
                                self._empty_backoff_level = 0
                                self._set_pause("cola_vacia")
                                delay = self._get_current_empty_backoff_delay()
                                disp_logger.info(
                                    f"📭 [COLA VACÍA] Sin registros disponibles para '{s_nom}'. "
                                    f"Activando pausa coordinada de todos los workers ({self.workers_count} slots). "
                                    f"Centinela Explorador activado (espera inicial: {int(delay)}s)."
                                )
                        else:
                            if "cola_vacia" in self._pause_reasons:
                                self._clear_pause("cola_vacia")
                            self._empty_backoff_level = 0

                        if resp_q:
                            resp_q.put(lote or [])

                    elif action == "scout_probe":
                        b_size = msg.get("batch_size", self.batch_size)
                        prio = msg.get("prioridad", self.prioridad)
                        s_nom = msg.get("scraper_nombre", self.scraper_name)
                        s_sin = msg.get("solo_sin_coincidencia", self.solo_sin_coincidencia)
                        lote = self.db_adapter.reservar_lote(
                            batch_size=b_size,
                            prioridad=prio,
                            scraper_nombre=s_nom,
                            solo_sin_coincidencia=s_sin
                        )
                        if lote and len(lote) > 0:
                            with self._scout_lock:
                                self._scout_prefetched_lote = lote
                                self._empty_backoff_level = 0
                            self._clear_pause("cola_vacia")
                            disp_logger.info(
                                f"🎯 [CENTINELA] Sondeo exitoso: {len(lote)} registros reservados. "
                                "Pausa de cola vacía liberada. Reanudando workers."
                            )
                            if resp_q:
                                resp_q.put({"encontrados": True, "cantidad": len(lote), "prox_delay": 0.0})
                        else:
                            self._empty_backoff_level += 1
                            prox_delay = self._get_current_empty_backoff_delay()
                            disp_logger.info(
                                f"📭 [CENTINELA] Sondeo explorador sin registros. Próximo intento en {int(prox_delay)}s "
                                f"(Nivel {self._empty_backoff_level + 1})."
                            )
                            if resp_q:
                                resp_q.put({"encontrados": False, "cantidad": 0, "prox_delay": prox_delay})

                    elif action == "persistir_resultados":
                        resultados = msg.get("resultados", [])
                        if resultados:
                            self._write_buffer.extend(resultados)
                            for r in resultados:
                                st = str(r.get("status", "")).lower()
                                if st in ("error", "failed", "fallido"):
                                    self._pending_stats["fallidos"] += 1
                                else:
                                    self._pending_stats["procesados"] += 1
                                    if st == "coincidencia":
                                        self._pending_stats["enriquecidos"] += 1

                            # Responder de inmediato al worker para desacoplar su ciclo de scraping
                            if resp_q:
                                resp_q.put(True)

                            # Si se alcanzó el tamaño umbral masivo, forzar flush inmediato
                            if len(self._write_buffer) >= self.buffer_flush_size:
                                self._flush_write_buffer(disp_logger)
                        else:
                            if resp_q:
                                resp_q.put(True)

                    elif action == "revertir_a_pendiente":
                        ids = msg.get("ids", [])
                        ok = self.db_adapter.revertir_a_pendiente(ids)
                        if resp_q:
                            resp_q.put(ok)

                    elif action == "liberar_huerfanos":
                        mins = msg.get("minutos", self.minutos_inactividad_huerfanos)
                        afectados = self.db_adapter.liberar_huerfanos(minutos_inactividad=mins)
                        if resp_q:
                            resp_q.put(afectados)

                    elif action == "obtener_estadisticas":
                        stats = self.db_adapter.obtener_estadisticas()
                        if resp_q:
                            resp_q.put(stats)

                    else:
                        disp_logger.warning(f"Acción IPC desconocida: {action}")
                        if resp_q:
                            resp_q.put({"error": f"Acción desconocida: {action}"})

                except Exception as e:
                    disp_logger.error(f"Fallo despachando acción '{action}' para slot {slot_id}: {e}", exc_info=True)
                    if resp_q:
                        resp_q.put({"error": str(e)})

        finally:
            # Al detenerse el despachador, vaciado atómico garantizado de cualquier remanente en RAM
            if self._write_buffer:
                disp_logger.info(f"💾 Vaciando buffer final de {len(self._write_buffer)} registros remanentes...")
                self._flush_write_buffer(disp_logger)

            if hasattr(self.db_adapter, "guardar_session_stats"):
                if self._pending_stats["procesados"] > 0 or self._pending_stats["fallidos"] > 0:
                    try:
                        self.db_adapter.guardar_session_stats(
                            self._pending_stats["procesados"],
                            self._pending_stats["enriquecidos"],
                            self._pending_stats["fallidos"]
                        )
                        self._pending_stats = {"procesados": 0, "enriquecidos": 0, "fallidos": 0}
                    except Exception as e_stats:
                        disp_logger.warning(f"Aviso al guardar estadísticas finales: {e_stats}")

    def _watchdog_sweeper_loop(self, sweep_interval_sec: Optional[float] = None):
        """Centinela que ejecuta el barrido de huérfanos a través del despachador unificado."""
        wd_logger = logging.getLogger("WatchdogSweeper")
        interval = sweep_interval_sec or self.watchdog_sweep_interval
        resp_wd_q = Queue()
        self.slot_response_queues[0] = resp_wd_q

        while not self.stop_event.is_set():
            for _ in range(int(interval)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

            if self.stop_event.is_set():
                break

            try:
                self.db_request_queue.put({
                    "action": "liberar_huerfanos",
                    "slot_id": 0,
                    "minutos": self.minutos_inactividad_huerfanos
                })
                rescatados = resp_wd_q.get(timeout=30.0)
                if isinstance(rescatados, int) and rescatados > 0:
                    wd_logger.info(f"🧹 [WATCHDOG] {rescatados} registros huérfanos liberados a estado 'pendiente'.")
            except Exception as e:
                wd_logger.error(f"Error en barrido de huérfanos IPC: {e}")

    def _scout_probe_loop(self):
        """
        Centinela Explorador (Scout Probe).
        Cuando la cola se encuentra vacía, todos los workers se pausan coordinadamente.
        Este hilo centinela es el ÚNICO que solicita sondeos exploratorios a MySQL a través
        del despachador centralizado, respetando una escalera de backoff progresivo
        (60s -> 180s -> 300s -> 600s -> máx 900s / 15 min).
        Evita el 'Thundering Herd' y el bombardeo continuo a la base de datos VPS.
        Si detecta nuevos registros, los pre-reserva en memoria y reactiva los workers.
        """
        scout_logger = logging.getLogger("CentinelaExplorador")
        resp_scout_q = Queue()
        self.slot_response_queues[-1] = resp_scout_q

        while not self.stop_event.is_set():
            if "cola_vacia" not in self._pause_reasons:
                time.sleep(1.0)
                continue

            delay = self._get_current_empty_backoff_delay()
            scout_logger.info(
                f"🔭 [CENTINELA] Cola vacía. Workers en reposo pasivo. Próximo sondeo en {int(delay)}s "
                f"(Escalera Nivel {self._empty_backoff_level + 1})..."
            )

            # Esperar el tiempo de backoff respetando interrupciones de parada o despausa
            for _ in range(int(delay)):
                if self.stop_event.is_set() or "cola_vacia" not in self._pause_reasons:
                    break
                time.sleep(1.0)

            if self.stop_event.is_set() or "cola_vacia" not in self._pause_reasons:
                continue

            # Enviar solicitud de sondeo explorador único a través del despachador
            scout_logger.info(
                f"🔍 [CENTINELA] Ejecutando sondeo explorador único a MySQL para '{self.scraper_name}'..."
            )
            try:
                self.db_request_queue.put({
                    "action": "scout_probe",
                    "slot_id": -1,
                    "batch_size": self.batch_size,
                    "prioridad": self.prioridad,
                    "scraper_nombre": self.scraper_name,
                    "solo_sin_coincidencia": self.solo_sin_coincidencia
                })
                res = resp_scout_q.get(timeout=60.0)
                if isinstance(res, dict) and res.get("encontrados"):
                    scout_logger.info(
                        f"🎯 [CENTINELA] ¡Nuevos registros detectados! ({res.get('cantidad')} obtenidos). "
                        "Lote pre-reservado en memoria. Workers reactivados sin consulta repetida."
                    )
                else:
                    prox = res.get("prox_delay", delay) if isinstance(res, dict) else delay
                    scout_logger.info(
                        f"📭 [CENTINELA] Cola continúa vacía. Próximo sondeo en {int(prox)}s."
                    )
            except Exception as e:
                scout_logger.error(f"❌ [CENTINELA] Error durante sondeo explorador: {e}")
                time.sleep(10.0)

    def _metrics_dashboard_loop(self):
        """Tablero de control en tiempo real."""
        stats = {
            "completados": 0,
            "no_coincidencias": 0,
            "errores": 0,
            "reciclados": 0,
            "latencias": [],
            "t0": time.time()
        }
        last_print = time.time()

        while not self.stop_event.is_set():
            try:
                msg = self.stats_queue.get(timeout=1.0)
                tipo = msg.get("tipo")
                if tipo == "completado":
                    stats["completados"] += 1
                    stats["latencias"].append(msg.get("latency", 0))
                elif tipo == "no_coincidencia":
                    stats["no_coincidencias"] += 1
                    stats["latencias"].append(msg.get("latency", 0))
                elif tipo == "error":
                    stats["errores"] += 1
                elif tipo == "worker_exit" and msg.get("recycled"):
                    stats["reciclados"] += 1
            except Exception:
                pass

            now = time.time()
            intervalo_print = 300.0 if "horario" in self._pause_reasons else 25.0
            if now - last_print >= intervalo_print:
                total_proc = stats["completados"] + stats["no_coincidencias"] + stats["errores"]
                elapsed = now - stats["t0"]
                rpm = round((total_proc / elapsed) * 60, 1) if elapsed > 0 else 0
                avg_lat = round(sum(stats["latencias"][-50:]) / len(stats["latencias"][-50:]), 2) if stats["latencias"] else 0
                status_operativo = self._obtener_estado_pausa()

                prio_str = "Auto (P1->P2->P3)" if self.prioridad is None else f"P{self.prioridad}"
                filtro_str = "Solo Sin Coincidencia (Telcos)" if self.solo_sin_coincidencia else "Estándar (TTL 7 días)"

                print("\n" + "=" * 72)
                print(f"📊 TABLERO 24/7 SUPERVISOR INDUSTRIAL | {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  • Scraper Activo:           {self.scraper_name.upper()} ({prio_str})")
                print(f"  • Modo Filtrado:            {filtro_str}")
                print(f"  • Estado Operativo:         {status_operativo}")
                print(f"  • Workers en paralelo:      {self.workers_count} slots activos")
                print(f"  • Total procesados:         {total_proc:,} registros | Ritmo: {rpm} reg/min")
                print(f"  • Coincidencias positivas:  {stats['completados']:,}")
                print(f"  • Sin coincidencias:        {stats['no_coincidencias']:,}")
                print(f"  • Errores controlados:      {stats['errores']:,}")
                print(f"  • Rotaciones preventivas:   {stats['reciclados']:,} relevos anti-leak")
                print(f"  • Latencia promedio:        {avg_lat}s por consulta")
                buf_len = len(self._write_buffer)
                buf_age = int(now - self._last_buffer_flush)
                print(f"  • Buffer en RAM (Binlog):   {buf_len}/{self.buffer_flush_size} en espera (flush cada {int(self.buffer_max_delay)}s / hace {buf_age}s)")
                print("=" * 72 + "\n", flush=True)
                last_print = now

    def _spawn_worker(self, slot_id: int, generation: int):
        if slot_id not in self.slot_response_queues:
            self.slot_response_queues[slot_id] = Queue()

        p = Process(
            target=worker_lifecycle_process,
            args=(
                slot_id,
                generation,
                self.scraper_name,
                self.batch_size,
                self.max_queries_worker,
                self.stop_event,
                self.pause_event,
                self.stats_queue,
                self.prioridad,
                self.delay_min,
                self.delay_max,
                self.scraper_kwargs,
                self.solo_sin_coincidencia,
                self.db_request_queue,
                self.slot_response_queues[slot_id]
            ),
            name=f"WorkerSlot-{slot_id}-G{generation}"
        )
        p.start()
        self.worker_slots[slot_id] = {"process": p, "gen": generation}
        logger.info(f"🚀 Worker Slot {slot_id} lanzado ({self.scraper_name.upper()}, Gen {generation}, PID {p.pid}).")

    def ejecutar(self):
        """Punto de entrada principal para iniciar la supervisión continua."""
        estado_hc = self.politica_horario.obtener_estado()
        desc_hc = "IGNORADO (--forzar-horario)" if self.forzar_horario else estado_hc["descripcion"]

        print("*" * 75)
        print("🛡️  INICIANDO SUPERVISOR INDUSTRIAL 24/7 (ARQUITECTURA HEXAGONAL)")
        print(f"  • Scraper asignado:       {self.scraper_name.upper()}")
        print(f"  • Base de datos VPS:      {config.VPS_DBHOST}:{config.VPS_DBPORT}/{config.VPS_DBNAME}")
        tabla_str = f"cola_automatizacion (auto_id: {self.auto_id})" if self.queue_type == "cola_automatizacion" else config.VPS_DB_TABLE
        print(f"  • Origen de Cola:         {self.queue_type.upper()}")
        print(f"  • Tabla destino:          {tabla_str}")
        print(f"  • Slots concurrentes:     {self.workers_count} workers")
        print(f"  • Rotación preventiva:    Cada {self.max_queries_worker} consultas por worker")
        print(f"  • Buffer de Persistencia: {self.buffer_flush_size} registros / máx {int(self.buffer_max_delay)}s (1 commit masivo)")
        print(f"  • Heartbeat Throttling:   Cada {int(self.heartbeat_interval)}s")
        print(f"  • Stats Throttling:       Cada {int(self.stats_flush_interval)}s")
        print(f"  • Horario Comercial:      {desc_hc}")
        print(f"  • Circuit Breaker:        Activo")
        print(f"  • Watchdog Sweeper:       Activo (Barrido cada {int(self.watchdog_sweep_interval)}s)")
        print(f"  • Centinela Cola Vacía:   Backoff Progresivo (60s -> 3m -> 5m -> 10m -> máx {int(self.empty_queue_pause_max_sec / 60)}min)")
        print("*" * 75)

        # 1. Chequeo e inicialización del adaptador único de base de datos para toda la máquina
        try:
            if self.queue_type == "cola_automatizacion":
                self.db_adapter = ColaAutomatizacionAdapter(
                    pool_size=1,
                    pool_name=f"sup_pc_{os.getpid()}",
                    auto_id=self.auto_id,
                    pc_id=self.pc_id
                )
            else:
                self.db_adapter = MySQLQueueAdapter(pool_size=1, pool_name=f"sup_pc_{os.getpid()}")
            stats = self.db_adapter.obtener_estadisticas()
            pendientes = stats.get(f"{self.scraper_name}_pendiente", 0)
            total_global = stats.get("pendiente", 0)
            print(f"Conexión con VPS confirmada (1 socket permanente). Registros pendientes para '{self.scraper_name}': {pendientes:,} (Total global en cola: {total_global:,})\n")
        except Exception as e:
            logger.critical(f"Error conectando con la base de datos central VPS: {e}")
            return

        # Iniciar hilo despachador IPC centralizado (1 sola conexión activa para todos los workers)
        disp_thread = threading.Thread(target=self._db_dispatcher_loop, daemon=True, name="DBDispatcherThread")
        disp_thread.start()

        # 2. Configurar manejo de señales
        def handle_shutdown(signum, frame):
            print("\n🛑 Señal de detención recibida. Notificando a todos los workers para apagado limpio...")
            self.stop_event.set()

        signal.signal(signal.SIGINT, handle_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_shutdown)

        # 3. Inicializar pausa preventiva si arranca fuera de horario comercial
        if not self.forzar_horario and "iris" in self.scraper_name and not estado_hc["abierto"]:
            logger.warning(
                f"⏸️ [HORARIO COMERCIAL] Inicio fuera de ventana operativa: {estado_hc['descripcion']}. "
                "Los workers iniciarán en reposo hasta la reapertura comercial."
            )
            self._set_pause("horario")

        # 4. Iniciar demonio Tor o ProxyPool único compartido si el scraper es Claro, Movistar, Personal o Datuar
        tor_daemon = None
        if any(op in self.scraper_name for op in ("claro", "movistar", "personal", "datuar")):
            if self.scraper_kwargs.get("use_proxy_pool", config.PROXY_POOL_ENABLED):
                from adapters.network.proxy_pool import ProxyPoolManager
                logger.info("🌐 [SUPERVISOR] Inicializando caché y bootstrap inicial de ProxyPoolManager...")
                ppm = ProxyPoolManager.get_instance()
                ppm.bootstrap(min_proxies=2, max_wait_sec=20)
                ppm.start_background_feeder()
                self.scraper_kwargs["proxy_pool_shared"] = True
            elif self.scraper_kwargs.get("use_tor", config.TOR_ENABLED):
                from adapters.network.tor_controller import TorController
                tor_daemon = TorController(
                    socks_port=config.TOR_SOCKS_PORT_BASE,
                    control_port=config.TOR_CONTROL_PORT_BASE,
                    is_owner=True
                )
                logger.info("🧅 [SUPERVISOR] Verificando y asegurando demonio Tor único compartido (Stream Isolation)...")
                if not tor_daemon.ensure_running(timeout_sec=180):
                    logger.error("❌ [SUPERVISOR] No se pudo completar el bootstrap de Tor centralizado.")
                else:
                    logger.info("✅ [SUPERVISOR] Demonio Tor centralizado 100% operativo. Propagando tor_external_daemon=True a workers.")
                self.scraper_kwargs["tor_external_daemon"] = True

        # 5. Iniciar hilos centinelas de soporte
        cb_thread = threading.Thread(target=self._circuit_breaker_loop, daemon=True, name="CircuitBreakerThread")
        cb_thread.start()

        hc_thread = threading.Thread(target=self._horario_comercial_loop, daemon=True, name="HorarioComercialThread")
        hc_thread.start()

        wd_thread = threading.Thread(target=self._watchdog_sweeper_loop, daemon=True, name="WatchdogSweeperThread")
        wd_thread.start()

        scout_thread = threading.Thread(target=self._scout_probe_loop, daemon=True, name="ScoutProbeThread")
        scout_thread.start()

        dash_thread = threading.Thread(target=self._metrics_dashboard_loop, daemon=True, name="DashboardThread")
        dash_thread.start()

        # 6. Lanzamiento escalonado de workers iniciales
        logger.info(f"Lanzando {self.workers_count} workers iniciales de forma escalonada...")
        for s_id in range(1, self.workers_count + 1):
            if self.stop_event.is_set():
                break
            self._spawn_worker(s_id, generation=1)
            time.sleep(1.5)

        print("\n✅ Todos los workers se encuentran en ejecución permanente.")
        print("Presiona Ctrl+C en cualquier momento para detener de forma ordenada.\n")

        # 7. Bucle de supervisión y autoregeneración
        while not self.stop_event.is_set():
            time.sleep(2.0)

            for s_id in range(1, self.workers_count + 1):
                slot_info = self.worker_slots.get(s_id)
                if not slot_info:
                    continue

                proc: Process = slot_info["process"]
                gen: int = slot_info["gen"]

                if not proc.is_alive():
                    proc.join(timeout=1.0)
                    logger.info(f"Slot {s_id} (Gen {gen}) concluyó.")

                    if not self.stop_event.is_set():
                        nueva_gen = gen + 1
                        logger.info(f"🔄 Auto-Spawn: Reemplazando Slot {s_id} con Generación {nueva_gen}...")
                        time.sleep(1.0)
                        self._spawn_worker(s_id, nueva_gen)

        # 8. Apagado ordenado
        logger.info("Esperando que los workers activos completen sus lotes en curso...")
        for s_id, slot_info in self.worker_slots.items():
            proc: Process = slot_info["process"]
            if proc.is_alive():
                proc.join(timeout=15.0)
                if proc.is_alive():
                    logger.warning(f"Slot {s_id} no respondió en 15s. Forzando terminación...")
                    proc.terminate()

        # Detener hilo despachador de base de datos tras la conclusión de los workers
        time.sleep(0.5)
        self._dispatcher_stop.set()
        disp_thread.join(timeout=5.0)

        if tor_daemon is not None:
            logger.info("🧅 Deteniendo demonio Tor compartido...")
            tor_daemon.stop()

        print("\n" + "*" * 75)
        print("🏁 Supervisión 24/7 finalizada ordenadamente. Base de datos protegida.")
        print("*" * 75)
