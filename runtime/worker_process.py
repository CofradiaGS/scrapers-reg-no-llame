# -*- coding: utf-8 -*-
"""
Runtime: Ciclo de Vida de Worker Individual (Aislado en Subproceso)
Ejecuta el caso de uso ProcesarLoteUseCase desacoplado mediante los adaptadores inyectados:
- Adaptador de Cola (MySQLQueueAdapter con pool aislado por PID).
- Adaptador de Scraper (obtenido dinámicamente de ScraperRegistry).
- Control de rotación preventiva anti-leak de memoria RAM.
- Circuit breaker reactivo ante cortes de VPN/Red.
"""
import os
import sys
import time
import signal
import random
import logging
from typing import Optional, Dict, Any
from multiprocessing import Queue, Event

from core.use_cases.process_batch_use_case import ProcesarLoteUseCase
from core.domain.exceptions import FueraDeHorarioComercialException
from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
from adapters.scrapers.registry import ScraperRegistry

def worker_lifecycle_process(
    worker_slot: int,
    generation: int,
    scraper_name: str,
    batch_size: int,
    max_queries: int,
    stop_event: Event,
    pause_event: Event,
    stats_queue: Queue,
    prioridad: Optional[int] = None,
    delay_min: float = 1.5,
    delay_max: float = 2.5,
    scraper_kwargs: Optional[Dict[str, Any]] = None,
    solo_sin_coincidencia: bool = False,
    db_request_queue: Optional[Queue] = None,
    db_response_queue: Optional[Queue] = None
):
    """
    Función de ejecución en subproceso de un Worker individual:
    - Slot y Generación para trazabilidad estricta (ej: W1-G2).
    - Desacoplamiento total: delega el flujo de negocio al caso de uso ProcesarLoteUseCase.
    - Soporta IPCWorkerQueueAdapter (0 conexiones directas a MySQL) o MySQLQueueAdapter directo.
    - Manejo de señales: ignora SIGINT para que el supervisor coordine el apagado seguro.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    worker_tag = f"W{worker_slot}-G{generation}"
    w_log = logging.getLogger(worker_tag)
    w_log.info(
        f"[{worker_tag}] Iniciando worker (Scraper: {scraper_name.upper()}, PID: {os.getpid()}). "
        f"Límite de rotación preventiva: {max_queries} consultas."
    )

    cola_repo = None
    scraper_engine = None
    consultas_realizadas = 0

    try:
        # 1. Adaptador de Cola (IPC Despachador hacia Supervisor si está provisto, o MySQL directo)
        if db_request_queue is not None and db_response_queue is not None:
            from adapters.queue.ipc_adapter import IPCWorkerQueueAdapter
            cola_repo = IPCWorkerQueueAdapter(
                slot_id=worker_slot,
                request_queue=db_request_queue,
                response_queue=db_response_queue
            )
            w_log.info(f"[{worker_tag}] Conectado a Supervisor mediante IPC (0 sockets remotos directos a BD).")
        else:
            cola_repo = MySQLQueueAdapter(
                pool_size=1,
                pool_name=f"pool_{worker_slot}_{generation}_{os.getpid()}"
            )

        # 2. Adaptador de Motor de Scraping dinámico vía Registry
        kwargs = dict(scraper_kwargs or {})
        if "worker_slot" not in kwargs:
            kwargs["worker_slot"] = worker_slot
        scraper_engine = ScraperRegistry.obtener(scraper_name, **kwargs)
        scraper_engine.iniciar()

        # Espera inicial si el supervisor arrancó en estado de pausa (ej: fuera de horario comercial o caída de VPN)
        while pause_event.is_set() and not stop_event.is_set():
            w_log.warning(f"[{worker_tag}] Supervisor en pausa (esperando horario comercial o red/VPN). En reposo...")
            for _ in range(5):
                if stop_event.is_set() or not pause_event.is_set():
                    break
                time.sleep(1)

        if stop_event.is_set():
            return

        # 3. Autenticación inicial del scraper
        w_log.info(f"[{worker_tag}] Autenticando en motor {scraper_name.upper()}...")
        try:
            if not scraper_engine.autenticar():
                err_msg = f"[{worker_tag}] No se pudo autenticar en el motor {scraper_name.upper()}."
                w_log.error(err_msg)
                stats_queue.put({
                    "tipo": "error_login",
                    "slot": worker_slot,
                    "gen": generation,
                    "scraper": scraper_name,
                    "msg": err_msg
                })
                return
        except FueraDeHorarioComercialException as e:
            w_log.warning(f"[{worker_tag}] Autenticación pospuesta por horario comercial: {e}")
            while pause_event.is_set() and not stop_event.is_set():
                time.sleep(2)
            if stop_event.is_set():
                return

        w_log.info(f"[{worker_tag}] ✅ Sesión activa. Iniciando consumo continuo de cola.")

        # 4. Inyección de dependencias en el Caso de Uso de Aplicación
        enacom_adapter = None
        try:
            from adapters.enacom.enacom_adapter import EnacomBlockAdapter
            enacom_adapter = EnacomBlockAdapter()
        except Exception as e_enacom:
            w_log.warning(f"[{worker_tag}] No se pudo inicializar EnacomBlockAdapter: {e_enacom}")

        use_case = ProcesarLoteUseCase(
            cola_repo=cola_repo,
            scraper_engine=scraper_engine,
            operator_lookup=enacom_adapter
        )

        # 5. Callback de reporte de progreso por ítem procesado
        def notificar_item(item: Dict[str, Any]):
            status = item.get("status", "sin_coincidencia")
            tipo_stat = "completado" if status == "coincidencia" else (
                "no_coincidencia" if status in ("sin_coincidencia", "salteado") else "error"
            )
            lat = item.get("latencia", 0.0)
            item_id = item.get("id")
            ani = item.get("ani")
            sig_scraper = item.get("scraper_actual")
            sig_estado = item.get("estado")
            desc = item.get("descripcion", "")[:70]

            stats_queue.put({
                "tipo": tipo_stat,
                "slot": worker_slot,
                "scraper": scraper_name,
                "latency": lat
            })

            if status == "coincidencia":
                w_log.info(f"[{worker_tag}] ID:{item_id} | ANI:{ani} -> 🎯 COINCIDENCIA ({lat}s) ➔ [{sig_scraper}:{sig_estado}] | {desc}")
            elif status == "sin_coincidencia":
                w_log.info(f"[{worker_tag}] ID:{item_id} | ANI:{ani} -> ℹ️ SIN COINCIDENCIA ({lat}s) ➔ [{sig_scraper}:{sig_estado}]")
            elif status == "salteado":
                w_log.info(f"[{worker_tag}] ID:{item_id} | ANI:{ani} -> ⏩ SALTEADO ({lat}s) ➔ [{sig_scraper}:{sig_estado}] | {desc}")
            else:
                w_log.error(f"[{worker_tag}] ID:{item_id} | ANI:{ani} -> ❌ ERROR ({lat}s): {desc}")

        # 6. Bucle principal de consumo de lotes
        while not stop_event.is_set():
            if consultas_realizadas >= max_queries:
                w_log.info(
                    f"[{worker_tag}] 🔄 Cuota de rotación preventiva alcanzada "
                    f"({consultas_realizadas}/{max_queries}). Relevo limpio anti-leak."
                )
                break

            # Si el supervisor activa pausa (ej: fin de jornada comercial a las 21:00 o caída de red/VPN)
            if pause_event.is_set():
                w_log.warning(f"[{worker_tag}] Supervisor en pausa (fuera de horario comercial o red/VPN). En reposo...")
                for _ in range(5):
                    if stop_event.is_set() or not pause_event.is_set():
                        break
                    time.sleep(1)
                continue

            # Calcular tamaño de lote sin exceder la cuota de rotación preventiva
            batch_to_claim = min(batch_size, max_queries - consultas_realizadas)

            # Ejecución del Caso de Uso (Atómico: Reclamo -> Scraping -> Dominio -> Persistencia)
            num_proc, sin_proc = use_case.ejecutar_lote(
                batch_size=batch_to_claim,
                prioridad=prioridad,
                on_item_procesado=notificar_item,
                should_stop=stop_event.is_set,
                solo_sin_coincidencia=solo_sin_coincidencia
            )

            if num_proc == 0:
                # Cola vacía momentáneamente para este scraper/prioridad
                w_log.info(f"[{worker_tag}] Sin registros disponibles para '{scraper_name}'. Esperando 6s...")
                for _ in range(6):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
                continue

            consultas_realizadas += num_proc
            w_log.info(f"[{worker_tag}] 💾 Lote de {num_proc} procesado y guardado. (Total gen: {consultas_realizadas}/{max_queries})")

            # Jitter de cortesía entre lotes
            delay = random.uniform(delay_min, delay_max)
            time.sleep(delay)

    except Exception as e:
        w_log.critical(f"[{worker_tag}] Fallo crítico en proceso worker: {e}", exc_info=True)
    finally:
        w_log.info(f"[{worker_tag}] Cerrando recursos del scraper...")
        if scraper_engine:
            try:
                scraper_engine.cerrar()
            except Exception:
                pass

        stats_queue.put({
            "tipo": "worker_exit",
            "slot": worker_slot,
            "gen": generation,
            "scraper": scraper_name,
            "consultas": consultas_realizadas,
            "recycled": consultas_realizadas >= max_queries
        })
        w_log.info(f"[{worker_tag}] Proceso finalizado. Total consultas ejecutadas: {consultas_realizadas}.")
