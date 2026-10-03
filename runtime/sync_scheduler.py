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
        check_interval_sec: float = 20.0,
        on_pull_success: Optional[Callable[[int], None]] = None
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
        self.sync_push_window_end_hour = sync_push_window_end_hour if sync_push_window_end_hour is not None else getattr(config, "SYNC_PUSH_WINDOW_END_HOUR", 8)
        self.sync_push_hour = sync_push_hour if sync_push_hour is not None else self.sync_push_window_start_hour
        self.sync_chunk_size = sync_chunk_size if sync_chunk_size is not None else getattr(config, "SYNC_CHUNK_SIZE", 5000)
        self.sync_sweep_wait_sec = sync_sweep_wait_sec if sync_sweep_wait_sec is not None else getattr(config, "SYNC_SWEEP_WAIT_SEC", 10.0)
        self.sync_retention_days = sync_retention_days if sync_retention_days is not None else getattr(config, "SYNC_RETENTION_DAYS", 7)
        self.check_interval_sec = check_interval_sec
        self.on_pull_success = on_pull_success

        self._pull_use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_repo, local_repo=self.local_repo)
        self._push_use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_repo, local_repo=self.local_repo)

        self._ultimo_push_fecha: Optional[date] = None
        self._sync_lock = threading.Lock()
        self._ultimo_pull_vacio_time: float = 0.0

    def esta_en_ventana_push(self, dt: Optional[datetime] = None) -> bool:
        """Verifica si el horario actual se encuentra en la ventana nocturna de push (por defecto 00:00 a 08:00 hs)."""
        now = dt or datetime.now()
        inicio = self.sync_push_window_start_hour
        fin = self.sync_push_window_end_hour
        if inicio < fin:
            return inicio <= now.hour < fin
        elif inicio > fin:
            return now.hour >= inicio or now.hour < fin
        else:
            return True

    def ejecutar_pull_inmediato(self, forzar: bool = True) -> Dict[str, Any]:
        """Ejecuta un pull de forma síncrona y atómica bajo lock."""
        with self._sync_lock:
            return self._pull_use_case.ejecutar(
                tipo_cola=self.tipo_cola,
                limit=self.pull_chunk_size,
                forzar=forzar,
                umbral_minimo=self.low_watermark,
                auto_id=self.auto_id,
                pc_id=self.pc_id,
                scraper_actual=self.scraper_actual
            )

    def ejecutar_push_inmediato(self, verificar_ventana: bool = False) -> Dict[str, Any]:
        """
        Ejecuta un push de forma síncrona y atómica bajo lock.
        :param verificar_ventana: Si es True, detiene la subida si la ventana nocturna finaliza (08:00 AM).
        """
        with self._sync_lock:
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
                should_stop=should_stop
            )
            if res.get("exito"):
                self._ultimo_push_fecha = datetime.now().date()
            return res

    def run(self):
        """Bucle continuo del centinela de sincronización."""
        logger.info(
            f"⏰ [SYNC SCHEDULER] Centinela de sincronización iniciado. "
            f"(Cola: {self.tipo_cola} | Watermark: <= {self.low_watermark:,} -> Pull {self.pull_chunk_size:,} | "
            f"Ventana Push Nocturno: {self.sync_push_window_start_hour:02d}:00 a {self.sync_push_window_end_hour:02d}:00 hs | "
            f"Chunks: {self.sync_chunk_size:,} | Retención: {self.sync_retention_days}d)."
        )

        # 1. Comprobación inicial de arranque: Si la cola local está vacía o baja, recargar
        try:
            pendientes_inicio = self.local_repo.contar_pendientes(tipo_cola=self.tipo_cola)
            if pendientes_inicio <= self.low_watermark:
                logger.info(f"💧 [INICIO STAGING] Cola local baja ({pendientes_inicio:,} tareas). Ejecutando recarga inicial...")
                res_ini = self.ejecutar_pull_inmediato(forzar=False)
                if res_ini.get("ejecutado") and res_ini.get("descargados", 0) > 0 and self.on_pull_success:
                    try:
                        self.on_pull_success(res_ini["descargados"])
                    except Exception as e_cb:
                        logger.warning(f"Aviso en callback on_pull_success inicial: {e_cb}")
        except Exception as e_ini:
            logger.warning(f"Aviso en comprobación inicial de staging: {e_ini}")

        while not self.stop_event.is_set():
            try:
                now = datetime.now()

                # --- 1. EVALUAR PUSH NOCTURNO (Ventana 00:00 a 08:00 hs) ---
                if self.esta_en_ventana_push(now):
                    listos_subir = self.local_repo.contar_listos_para_subir(tipo_cola=self.tipo_cola)
                    if listos_subir > 0:
                        logger.info(
                            f"🌙 [VENTANA PUSH {self.sync_push_window_start_hour:02d}:00-{self.sync_push_window_end_hour:02d}:00] "
                            f"Son las {now.hour:02d}:{now.minute:02d} hs y hay {listos_subir:,} registros listos para subir. "
                            f"Iniciando subida masiva a VPS..."
                        )
                        res_push = self.ejecutar_push_inmediato(verificar_ventana=True)
                        if res_push.get("exito"):
                            if res_push.get("interrumpido_por_ventana"):
                                logger.info(
                                    f"⏰ [FIN VENTANA] Ventana de subida cerrada ({self.sync_push_window_end_hour:02d}:00 hs). "
                                    f"Se subieron {res_push.get('total_subidos', 0):,} registros. "
                                    f"Los remanentes permanecen seguros en SQLite local y se sincronizarán en el próximo ciclo."
                                )
                            else:
                                logger.info(
                                    f"🎉 Push completado exitosamente: {res_push.get('total_subidos', 0):,} registros sincronizados al VPS."
                                )
                        else:
                            logger.error(
                                f"⚠️ Push nocturno reportó errores: {res_push.get('error')}. Reintentará en el próximo ciclo."
                            )


                # --- 2. EVALUAR WATERMARK PULL (Reabastecimiento) ---
                # Evitar bombardeo continuo si el VPS no tenía tareas recientemente (cooldown de 60s)
                time_since_empty = time.time() - self._ultimo_pull_vacio_time
                if time_since_empty >= 60.0:
                    pendientes = self.local_repo.contar_pendientes(tipo_cola=self.tipo_cola)
                    if pendientes <= self.low_watermark:
                        res_pull = self.ejecutar_pull_inmediato(forzar=False)
                        if res_pull.get("ejecutado"):
                            descargados = res_pull.get("descargados", 0)
                            if descargados == 0:
                                self._ultimo_pull_vacio_time = time.time()
                            elif descargados > 0 and self.on_pull_success:
                                try:
                                    self.on_pull_success(descargados)
                                except Exception as e_cb:
                                    logger.warning(f"Aviso en on_pull_success: {e_cb}")

            except Exception as e:
                logger.error(f"❌ Excepción no controlada en SyncSchedulerThread: {e}", exc_info=True)

            # Espera interrumpible
            for _ in range(int(self.check_interval_sec)):
                if self.stop_event.is_set():
                    break
                time.sleep(1.0)

        logger.info("🛑 [SYNC SCHEDULER] Centinela de sincronización detenido.")
