# -*- coding: utf-8 -*-
"""
Runtime: Hilo Centinela de Sincronización Staging <-> VPS (SyncSchedulerThread)
Arquitectura Hexagonal - Orquestador Asíncrono de Watermark y Push Nocturno

Opera de forma autónoma en segundo plano dentro del Supervisor:
1. Reabastecimiento Automático (Watermark):
   Monitorea cada 30 segundos si las tareas pendientes en SQLite local descienden
   por debajo de LOW_WATERMARK_THRESHOLD (2.000). Si es así, ejecuta de inmediato
   el pull matutino/diurno descargando 10.000 tareas en 1 sola consulta atómica con SKIP LOCKED.
2. Push Nocturno Masivo (20:00 hs):
   Al alcanzar la hora configurada (SYNC_PUSH_HOUR), ejecuta el volcado masivo en chunks
   de 5.000 filas por transacción atómica a MySQL VPS, barre remanentes y purga historial > 7 días.
3. Métodos síncronos públicos para CLI:
   Permite forzar `--sync-now` o `--pull-now` a demanda.
"""
import time
import logging
import threading
from datetime import datetime, date
from typing import Dict, Any, Optional, Callable

import config
from core.domain.schedule import PoliticaHorarioComercial
from core.ports.sync_port import ISyncRemoteRepoPort, ISyncLocalRepoPort
from core.use_cases.sync_pull_use_case import SincronizarPullMatutinoUseCase
from core.use_cases.sync_push_use_case import SincronizarPushNocturnoUseCase

logger = logging.getLogger("SyncScheduler")


class SyncSchedulerThread(threading.Thread):
    """Hilo planificador centinela para Watermark Pull y Push Nocturno."""

    def __init__(
        self,
        local_repo: ISyncLocalRepoPort,
        remote_repo: ISyncRemoteRepoPort,
        tipo_cola: str = "cola_automatizacion",
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        scraper_actual: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
        pull_chunk_size: Optional[int] = None,
        low_watermark: Optional[int] = None,
        sync_push_window_start_hour: Optional[int] = None,
        sync_push_window_end_hour: Optional[int] = None,
        sync_push_hour: Optional[int] = None,
        sync_chunk_size: Optional[int] = None,
        sync_sweep_wait_sec: Optional[float] = None,
        sync_retention_days: Optional[int] = None,
        check_interval_sec: float = 10.0,
        on_pull_success: Optional[Callable[[int], None]] = None,
        force_push: bool = False,
        is_paused_fn: Optional[Callable[[], bool]] = None
    ):
        super().__init__(name="SyncSchedulerThread", daemon=True)
        self.local_repo = local_repo
        self.remote_repo = remote_repo
        self.tipo_cola = (tipo_cola or "cola_automatizacion").lower().strip()
        self.auto_id = auto_id or getattr(config, "COLA_AUTO_ID", "telco_scraper")
        self.pc_id = pc_id or getattr(config, "WORKER_PC_ID", "PC-00")
        self.scraper_actual = scraper_actual or ("telcos" if self.tipo_cola == "cola_automatizacion" else "iris")

        self.stop_event = stop_event or threading.Event()
        self.pull_chunk_size = pull_chunk_size if pull_chunk_size is not None else getattr(config, "PULL_CHUNK_SIZE", 5000)
        self.low_watermark = low_watermark if low_watermark is not None else getattr(config, "LOW_WATERMARK_THRESHOLD", 2000)
        self.sync_push_window_start_hour = sync_push_window_start_hour if sync_push_window_start_hour is not None else getattr(config, "SYNC_PUSH_WINDOW_START_HOUR", 0)
        self.sync_push_window_end_hour = sync_push_window_end_hour if sync_push_window_end_hour is not None else getattr(config, "SYNC_PUSH_WINDOW_END_HOUR", 21)
        self.sync_push_hour = sync_push_hour if sync_push_hour is not None else self.sync_push_window_start_hour
        self.sync_chunk_size = sync_chunk_size if sync_chunk_size is not None else getattr(config, "SYNC_CHUNK_SIZE", 5000)
        self.sync_sweep_wait_sec = sync_sweep_wait_sec if sync_sweep_wait_sec is not None else getattr(config, "SYNC_SWEEP_WAIT_SEC", 10.0)
        self.sync_retention_days = sync_retention_days if sync_retention_days is not None else getattr(config, "SYNC_RETENTION_DAYS", 7)
        self.check_interval_sec = check_interval_sec
        self.on_pull_success = on_pull_success
        self.force_push = force_push
        self.is_paused_fn = is_paused_fn
        self.politica_horario = PoliticaHorarioComercial()

        self._pull_use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_repo, local_repo=self.local_repo)
        self._push_use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_repo, local_repo=self.local_repo)

        self._ultimo_push_fecha: Optional[date] = None
        # Desacoplamiento total: locks independientes para no bloquear Watermark Pull durante el Push
        self._pull_lock = threading.Lock()
        self._push_lock = threading.Lock()
        self._sync_lock = self._push_lock  # Retrocompatibilidad
        self._push_thread: Optional[threading.Thread] = None
        self._ultimo_pull_vacio_time: float = 0.0
        self._ultimo_vps_sweep_time: float = time.time()

    def esta_en_ventana_push(self, dt: Optional[datetime] = None) -> bool:
        """
        Verifica si el horario actual (o provisto) se encuentra en la ventana habilitada para push
        en horario de Argentina (por defecto 00:00 a 21:00 hs; veda de 21:00 a 00:00 hs).
        """
        now = dt or datetime.now()
        # Si dt es provisto sin tzinfo (ej. pruebas unitarias), se evalúa directamente su .hour.
        # Si dt es None o tiene tzinfo, se resuelve en la zona horaria oficial de Argentina.
        if dt is None or dt.tzinfo is not None:
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(getattr(config, "HORARIO_COMERCIAL_TIMEZONE", "America/Argentina/Buenos_Aires"))
                if dt is None:
                    now = datetime.now(tz)
                else:
                    now = dt.astimezone(tz)
            except Exception:
                pass

        inicio = self.sync_push_window_start_hour
        fin = self.sync_push_window_end_hour
        if inicio < fin:
            return inicio <= now.hour < fin
        elif inicio > fin:
            return now.hour >= inicio or now.hour < fin
        else:
            return True

    def ejecutar_pull_inmediato(self, forzar: bool = True) -> Dict[str, Any]:
        """Ejecuta un pull de forma síncrona y atómica bajo su propio lock de pull."""
        with self._pull_lock:
            return self._pull_use_case.ejecutar(
                tipo_cola=self.tipo_cola,
                limit=self.pull_chunk_size,
                forzar=forzar,
                umbral_minimo=self.low_watermark,
                auto_id=self.auto_id,
                pc_id=self.pc_id,
                scraper_actual=self.scraper_actual
            )

    def ejecutar_push_inmediato(self, verificar_ventana: bool = False, exigir_lote_completo: bool = False) -> Dict[str, Any]:
        """
        Ejecuta un push de forma síncrona y atómica bajo su propio lock de push.
        :param verificar_ventana: Si es True, detiene la subida si la ventana finaliza (21:00 hs).
        :param exigir_lote_completo: Si es True, solo sube bloques completos de chunk_size.
        """
        with self._push_lock:
            def should_stop() -> bool:
                if self.stop_event.is_set():
                    return True
                if verificar_ventana and not self.esta_en_ventana_push():
                    return True
                return False

            res = self._push_use_case.ejecutar(
                tipo_cola=self.tipo_cola,
                chunk_size=self.sync_chunk_size,
                sweep_wait_sec=self.sync_sweep_wait_sec,
                dias_retencion=self.sync_retention_days,
                should_stop=should_stop,
                exigir_lote_completo=exigir_lote_completo
            )
            if res.get("exito"):
                self._ultimo_push_fecha = datetime.now().date()
            return res

    def _run_push_drain_loop(self):
        """
        Bucle de drenaje continuo de lotes de 5.000 registros al VPS.
        Se ejecuta en un hilo desacoplado en segundo plano para NO bloquear
        el centinela de Watermark Pull ni la producción de los workers.
        Sube consecutivamente sin esperas de sondeo (polling) mientras existan >= 5.000 registros.
        """
        with self._push_lock:
            lote_num = 1
            while not self.stop_event.is_set():
                # 1. Comprobación de veda horaria (21:00 a 00:00 hs Argentina)
                if not self.force_push and not self.esta_en_ventana_push():
                    logger.info(
                        f"⏰ [VEDA PUSH ACTIVADA] Horario de veda (21:00 a 00:00 hs Argentina). "
                        f"Deteniendo subida continua. Remanentes permanecen seguros en SQLite local."
                    )
                    break

                # 2. Comprobación de volumen de lote completo
                listos = self.local_repo.contar_listos_para_subir(tipo_cola=self.tipo_cola)
                if listos < self.sync_chunk_size and not self.force_push:
                    logger.info(
                        f"🏁 [PUSH DRENAJE FINALIZADO] Registros listos ({listos:,}) < lote mínimo ({self.sync_chunk_size:,}). "
                        f"Subida detenida hasta acumular 5.000 nuevos registros. Producción continúa sin pausas."
                    )
                    break

                logger.info(
                    f"🚀 [PUSH CONTINUO #{lote_num}] {listos:,} registros listos. "
                    f"Subiendo lote de {self.sync_chunk_size:,} al VPS..."
                )

                def should_stop() -> bool:
                    if self.stop_event.is_set():
                        return True
                    if not self.force_push and not self.esta_en_ventana_push():
                        return True
                    return False

                res = self._push_use_case.ejecutar(
                    tipo_cola=self.tipo_cola,
                    chunk_size=self.sync_chunk_size,
                    sweep_wait_sec=0.0,
                    dias_retencion=self.sync_retention_days,
                    roundrobin_pause_sec=getattr(config, "SYNC_PUSH_ROUNDROBIN_PAUSE_SEC", 0.5),
                    should_stop=should_stop,
                    exigir_lote_completo=not self.force_push
                )

                if not res.get("exito"):
                    logger.error(f"❌ [PUSH ERROR] Falló subida en lote #{lote_num}: {res.get('error')}. Reintentará en el próximo ciclo.")
                    break

                subidos = res.get("total_subidos", 0)
                if subidos == 0:
                    break

                self._ultimo_push_fecha = datetime.now().date()
                lote_num += 1
                # Si todavía quedan >= 5000 registros, el ciclo while continúa INMEDIATAMENTE
                # sin pausas de polling, ejecutando los N ciclos de 5.000 consecutivos.

    def _esta_en_pausa_operativa(self) -> bool:
        """Determina si la máquina está pausada o fuera de horario comercial oficial."""
        if self.is_paused_fn and self.is_paused_fn():
            return True
        if not self.force_push and getattr(config, "HORARIO_COMERCIAL_ENABLED", True):
            if any(k in (self.scraper_actual or "").lower() for k in ("iris", "telco", "claro", "personal", "movistar")):
                if hasattr(self, "politica_horario") and not self.politica_horario.esta_en_horario():
                    return True
        return False

    def run(self):
        """Bucle continuo del centinela de sincronización."""
        logger.info(
            f"⏰ [SYNC SCHEDULER] Centinela de sincronización iniciado. "
            f"(Cola: {self.tipo_cola} | Watermark: <= {self.low_watermark:,} -> Pull {self.pull_chunk_size:,} | "
            f"Ventana Push: {self.sync_push_window_start_hour:02d}:00 a {self.sync_push_window_end_hour:02d}:00 hs (Veda 21-00 hs) | "
            f"Chunks: {self.sync_chunk_size:,} | Retención: {self.sync_retention_days}d)."
        )

        # 0. Reconciliación Automática de Arranque (Boot Sweeper en VPS):
        # Rescata inmediatamente cualquier registro que haya quedado huérfano de sesiones previas
        try:
            logger.info("🔍 [BOOT SWEEPER] Verificando y recuperando posibles registros huérfanos en el VPS...")
            timeout_huerfanos = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)(self.scraper_actual)
            rescatados_boot = self.remote_repo.liberar_huerfanos_vps(
                tipo_cola=self.tipo_cola,
                minutos_inactividad=timeout_huerfanos,
                auto_id=self.auto_id,
                pc_id=self.pc_id,
                scraper_actual=self.scraper_actual
            )
            if rescatados_boot > 0:
                logger.info(f"✨ [BOOT SWEEPER] {rescatados_boot:,} registros huérfanos rescatados a 'pendiente' en el VPS (umbral: {timeout_huerfanos}m / {timeout_huerfanos/60:.1f}h).")
        except Exception as e_boot:
            logger.warning(f"Aviso en verificación inicial de huérfanos VPS: {e_boot}")

        # 1. Comprobación inicial de arranque: Si la cola local está vacía o baja, recargar
        try:
            if not self._esta_en_pausa_operativa():
                pendientes_inicio = self.local_repo.contar_pendientes(tipo_cola=self.tipo_cola, scraper_actual=self.scraper_actual)
                if pendientes_inicio <= self.low_watermark:
                    logger.info(f"💧 [INICIO STAGING] Cola local baja ({pendientes_inicio:,} tareas). Ejecutando recarga inicial...")
                    res_ini = self.ejecutar_pull_inmediato(forzar=False)
                    if res_ini.get("ejecutado") and res_ini.get("descargados", 0) > 0 and self.on_pull_success:
                        try:
                            self.on_pull_success(res_ini["descargados"])
                        except Exception as e_cb:
                            logger.warning(f"Aviso en callback on_pull_success inicial: {e_cb}")
            else:
                logger.info("⏸️ [INICIO STAGING] Recarga inicial omitida: Supervisor en pausa o fuera de horario comercial.")
        except Exception as e_ini:
            logger.warning(f"Aviso en comprobación inicial de staging: {e_ini}")

        while not self.stop_event.is_set():
            try:
                now = datetime.now()

                # --- 1. EVALUAR WATERMARK PULL (Reabastecimiento de tareas - Prioridad #1 para no parar producción) ---
                time_since_empty = time.time() - self._ultimo_pull_vacio_time
                if time_since_empty >= 60.0:
                    if self._esta_en_pausa_operativa():
                        # Fuera de horario comercial o supervisor pausado: no reclamar tareas al VPS
                        pass
                    else:
                        pendientes = self.local_repo.contar_pendientes(tipo_cola=self.tipo_cola, scraper_actual=self.scraper_actual)
                        if pendientes <= self.low_watermark:
                            res_pull = self.ejecutar_pull_inmediato(forzar=False)
                            if res_pull.get("ejecutado"):
                                descargados = res_pull.get("descargados", 0)
                                insertados = res_pull.get("insertados", 0)
                                if descargados == 0 or insertados == 0:
                                    self._ultimo_pull_vacio_time = time.time()
                                elif descargados > 0 and self.on_pull_success:
                                    try:
                                        self.on_pull_success(descargados)
                                    except Exception as e_cb:
                                        logger.warning(f"Aviso en on_pull_success: {e_cb}")

                # --- 2. EVALUAR PUSH AL VPS (Drenaje continuo desacoplado de lotes de 5.000) ---
                listos_subir = self.local_repo.contar_listos_para_subir(tipo_cola=self.tipo_cola)
                en_ventana = self.esta_en_ventana_push(now)
                lote_completo_listo = listos_subir >= self.sync_chunk_size

                if (self.force_push or en_ventana) and (lote_completo_listo or self.force_push):
                    # Iniciar hilo de drenaje continuo si no se encuentra ya activo
                    if self._push_thread is None or not self._push_thread.is_alive():
                        motivo = "Forzado (--force-push)" if self.force_push else f"Lote completo alcanzado ({listos_subir:,} >= {self.sync_chunk_size:,})"
                        logger.info(
                            f"🚀 [PUSH VPS DISPARADO ({motivo})] {listos_subir:,} registros listos. "
                            f"Lanzando hilo de drenaje continuo en lotes de {self.sync_chunk_size:,} sin detener producción..."
                        )
                        self._push_thread = threading.Thread(
                            target=self._run_push_drain_loop,
                            name="SyncPushDrainWorker",
                            daemon=True
                        )
                        self._push_thread.start()

                # --- 3. EVALUAR BARRIDO DE HUÉRFANOS EN VPS (Auto-sanación periódica cada 10 min) ---
                if time.time() - self._ultimo_vps_sweep_time >= 600.0:
                    self._ultimo_vps_sweep_time = time.time()
                    try:
                        timeout_huerfanos = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)(self.scraper_actual)
                        resc = self.remote_repo.liberar_huerfanos_vps(
                            tipo_cola=self.tipo_cola,
                            minutos_inactividad=timeout_huerfanos,
                            auto_id=self.auto_id,
                            pc_id=self.pc_id,
                            scraper_actual=self.scraper_actual
                        )
                        if resc > 0:
                            logger.info(f"🧹 [WATCHDOG PERIÓDICO VPS] {resc:,} registros huérfanos liberados a 'pendiente' en el VPS (umbral: {timeout_huerfanos}m / {timeout_huerfanos/60:.1f}h).")
                    except Exception as e_sw:
                        logger.warning(f"Aviso en barrido periódico de huérfanos VPS: {e_sw}")

            except Exception as e:
                logger.error(f"❌ Excepción no controlada en SyncSchedulerThread: {e}", exc_info=True)

            # Espera interrumpible breve (intervalo ágil para reaccionar de inmediato)
            for _ in range(int(self.check_interval_sec)):
                if self.stop_event.is_set():
                    break
                time.sleep(1.0)

        # Esperar finalización ordenada del hilo de push si estuviera activo
        if self._push_thread and self._push_thread.is_alive():
            logger.info("⏳ Esperando que el hilo de subida continua finalice el chunk en curso...")
            self._push_thread.join(timeout=10.0)

        logger.info("🛑 [SYNC SCHEDULER] Centinela de sincronización detenido.")

