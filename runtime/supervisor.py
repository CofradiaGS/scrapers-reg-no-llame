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
        batch_size: int = 12,
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
        pc_id: Optional[str] = None
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

    def _horario_comercial_loop(self, check_interval: float = 10.0):
        """Monitor centinela de horario comercial para scrapers oficiales (IRIS Movistar)."""
        hc_logger = logging.getLogger("HorarioComercial")

        if self.forzar_horario or not ("iris" in self.scraper_name):
            return

        while not self.stop_event.is_set():
            estado = self.politica_horario.obtener_estado()
            esta_abierto = estado["abierto"]

            if not esta_abierto:
                if "horario" not in self._pause_reasons:
                    hc_logger.warning(
                        f"⏸️ [HORARIO COMERCIAL] Fin de jornada detectado. {estado['descripcion']}. "
                        "Pausando reclamo de lotes."
                    )
                    self._set_pause("horario")
            else:
                if "horario" in self._pause_reasons:
                    hc_logger.info(
                        f"🟢 [HORARIO COMERCIAL] Apertura comercial detectada. {estado['descripcion']}. "
                        "Reanudando workers automáticamente."
                    )
                    self._clear_pause("horario")

            for _ in range(int(check_interval)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

    def _db_dispatcher_loop(self):
        """
        Hilo despachador centralizado de base de datos.
        Mantiene la única conexión TCP/TLS activa de esta máquina hacia MySQL VPS,
        procesando peticiones de los workers locales de forma serializada y atómica.
        """
        disp_logger = logging.getLogger("DBDispatcher")
        disp_logger.info("📡 [DB DISPATCHER] Hilo despachador IPC iniciado (1 sola conexión MySQL activa para toda la PC).")

        while not self._dispatcher_stop.is_set():
            try:
                msg = self.db_request_queue.get(timeout=1.0)
            except Exception:
                continue

            action = msg.get("action")
            slot_id = msg.get("slot_id")
            resp_q = self.slot_response_queues.get(slot_id)

            try:
                if action == "reservar_lote":
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
                    if resp_q:
                        resp_q.put(lote)

                elif action == "persistir_resultados":
                    resultados = msg.get("resultados", [])
                    ok = self.db_adapter.persistir_resultados(resultados)
                    if resp_q:
                        resp_q.put(ok)

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

    def _watchdog_sweeper_loop(self, sweep_interval_sec: float = 300.0):
        """Centinela que ejecuta el barrido de huérfanos a través del despachador unificado."""
        wd_logger = logging.getLogger("WatchdogSweeper")
        resp_wd_q = Queue()
        self.slot_response_queues[0] = resp_wd_q

        while not self.stop_event.is_set():
            for _ in range(int(sweep_interval_sec)):
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
            if now - last_print >= 25.0:
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
        print(f"  • Horario Comercial:      {desc_hc}")
        print(f"  • Circuit Breaker:        Activo")
        print(f"  • Watchdog Sweeper:       Activo (Barrido cada 5m)")
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
            pendientes = stats.get(f"{self.scraper_name}_pendiente", stats.get("pendiente", 0))
            print(f"Conexión con VPS confirmada (1 socket permanente). Registros pendientes para '{self.scraper_name}': {pendientes:,}\n")
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
