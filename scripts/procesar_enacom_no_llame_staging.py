# -*- coding: utf-8 -*-
"""
Motor de Procesamiento Industrial: ENACOM Web para Registro No Llame
Arquitectura Offline-First con Staging en SQLite y Sincronización Automática con VPS MySQL

Principios Operativos Estrictos:
1. Reabastecimiento Masivo de 5.000 Registros: Almacena lotes de a 5.000 tareas en SQLite local
   (data/staging_local.db en modo WAL) para evitar saturación de disco y binlogs en MySQL VPS.
2. Cero Errores / Cero Datos Incompletos al VPS: Casi el 100% de las líneas en Argentina están
   asignadas a un prestador en ENACOM. Cualquier fallo es transitorio (caída de proxy o error de OCR).
   El sistema implementa reintentos automáticos con rotación de proxy en caliente. Si una línea
   no se puede certificar tras agotar los reintentos, se revierte a 'pendiente' en SQLite.
   JAMÁS se sube un registro con error, vacío o incompleto al VPS.
3. Persistencia Acumulativa Hexagonal: Al confirmar COINCIDENCIA, actualiza el namespace
   datos_json['enacom_web'] (prestador actual, prestador original, portabilidad, operador comercial)
   y añade 'enacom_web' a la lista acumulativa de fuentes sin sobreescribir datos previos.
4. Concurrencia Multiplexada: Pool de workers concurrentes ejecutando consultas sobre proxies volátiles.
"""

import sys
import os
import time
import json
import signal
import logging
import argparse
import threading
import collections
from typing import List, Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
from core.domain.entities import Linea, StatusScraping
from core.use_cases.sync_pull_use_case import SincronizarPullMatutinoUseCase
from core.use_cases.sync_push_use_case import SincronizarPushNocturnoUseCase
from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter
from adapters.queue.vps_sync_adapter import VPSSyncAdapter
from adapters.network.burst_proxy_manager import BurstProxyManager
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("EnacomIndustrialRunner")

# Flag global para detención ordenada
stop_requested = threading.Event()
_ctrl_c_count = 0


def handle_signal(sig, frame):
    stop_requested.set()
    print("\n🛑 [Ctrl+C] Interrupción detectada. Liberando tareas y cerrando navegadores...", flush=True)
    # 1. Revertir de inmediato cualquier tarea en_proceso a pendiente en SQLite
    try:
        import sqlite3
        conn = sqlite3.connect("data/staging_local.db", timeout=1.0)
        conn.execute("UPDATE tareas_staging SET estado_local = 'pendiente' WHERE estado_local = 'en_proceso' AND scraper_actual = 'enacom_web';")
        conn.commit()
        conn.close()
    except Exception:
        pass

    # 2. Terminar proceso y procesos Chromium hijos al instante vía taskkill
    try:
        import subprocess
        pid = os.getpid()
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1
        )
    except Exception:
        pass
    os._exit(0)


# Registro de adaptadores aislados por hilo de ejecución (Thread-Local)
_thread_local = threading.local()
_active_adapters = []
_active_adapters_lock = threading.Lock()
_last_proxy_wait_log = 0.0
_last_proxy_wait_lock = threading.Lock()


def obtener_adapter_worker(proxy_manager: Optional[BurstProxyManager] = None) -> Optional[EnacomWebAdapter]:
    """
    Instancia y retorna un EnacomWebAdapter aislado en el hilo actual de ejecución.
    BLINDAJE TOTAL: Si proxy_manager está activo, NUNCA permite peticiones directas desde la IP local.
    """
    if stop_requested.is_set():
        return None

    if not hasattr(_thread_local, "adapter") or _thread_local.adapter is None:
        proxy_init = None
        if proxy_manager:
            proxy_init = proxy_manager.obtener_proxy_vivo(timeout=3.0)
            while not proxy_init and not stop_requested.is_set():
                global _last_proxy_wait_log
                ahora = time.time()
                with _last_proxy_wait_lock:
                    if ahora - _last_proxy_wait_log >= 10.0:
                        _last_proxy_wait_log = ahora
                        logger.info(
                            f"🛡️  [ESCUDO IP ACTIVO] Harvester escaneando 19 fuentes de Argentina en vivo... "
                            f"(Protegiendo tu IP local: en espera del próximo proxy argentino validado)"
                        )
                time.sleep(2.0)
                proxy_init = proxy_manager.obtener_proxy_vivo(timeout=3.0)

            if stop_requested.is_set() or not proxy_init:
                return None

        try:
            ad = EnacomWebAdapter(headless=True, proxy=proxy_init)
            ad.iniciar()
            _thread_local.adapter = ad
            with _active_adapters_lock:
                _active_adapters.append(ad)
            logger.info(f"Worker Chromium conectado exitosamente vía proxy AR: {proxy_init or 'Directo'}")
        except Exception as e:
            logger.warning(f"Error al inicializar adapter ({proxy_init or 'directo'}): {e}")
            if proxy_init and proxy_manager:
                proxy_manager.report_proxy_result(proxy_init, False, reason="Init failed")
            try:
                ad.cerrar()
            except Exception:
                pass
            return None
    return getattr(_thread_local, "adapter", None)


def cerrar_todos_los_adapters():
    """Cierra de forma segura todos los navegadores Chromium levantados por los workers."""
    with _active_adapters_lock:
        for ad in _active_adapters:
            try:
                ad.cerrar()
            except Exception:
                pass
        _active_adapters.clear()


class IPPacer:
    """
    Controlador de cadencia cooperativa por IP/Proxy para blindar la reputación y evitar rate limits de ENACOM:
    1. Intervalo mínimo entre consultas consecutivas sobre la misma IP: min_interval_sec (default: 8.5s).
    2. Ventana deslizante (Sliding Window): Máximo 4 consultas cada 60 segundos por IP, 
       impidiendo matemáticamente que ENACOM dispare el 'límite de 5 consultas por minuto'.
    """
    _locks: Dict[str, threading.Lock] = {}
    _last_times: Dict[str, float] = {}
    _query_timestamps: Dict[str, collections.deque] = {}
    _master_lock = threading.Lock()

    @classmethod
    def esperar_turno(cls, proxy_url: Optional[str], min_interval_sec: float = 8.5):
        key = (proxy_url or "direct_ip").strip().lower()
        with cls._master_lock:
            if key not in cls._locks:
                cls._locks[key] = threading.Lock()
                cls._last_times[key] = 0.0
                cls._query_timestamps[key] = collections.deque(maxlen=10)
            lock = cls._locks[key]

        with lock:
            history = cls._query_timestamps[key]
            ahora = time.time()

            # 1. Ventana deslizante: Mantener solo marcas de tiempo de los últimos 60 segundos
            while history and (ahora - history[0]) > 60.0:
                history.popleft()

            # 2. Si ya hay 4 consultas en la ventana de 60 segundos, pausar hasta liberar la más antigua
            if len(history) >= 4:
                oldest = history[0]
                tiempo_espera = 60.5 - (ahora - oldest)
                if tiempo_espera > 0:
                    logger.info(
                        f"⏸️  [PACER PROTECCIÓN 5/MIN] Proxy '{key}': 4 consultas en los últimos 60s. "
                        f"Esperando {tiempo_espera:.1f}s para evitar rate limit de 5/min de ENACOM..."
                    )
                    time.sleep(tiempo_espera)
                    ahora = time.time()
                    while history and (ahora - history[0]) > 60.0:
                        history.popleft()

            # 3. Intervalo mínimo entre consultas individuales (8.5 segundos)
            elapsed = ahora - cls._last_times[key]
            if elapsed < min_interval_sec:
                time.sleep(min_interval_sec - elapsed)

            ahora_final = time.time()
            history.append(ahora_final)
            cls._last_times[key] = ahora_final


def procesar_registro_robusto(
    proxy_manager: Optional[BurstProxyManager],
    reg_id: int,
    ani: str,
    datos_previos: dict,
    max_intentos: int = 4
) -> Optional[dict]:
    """
    Ejecuta la consulta de la línea con tolerancia total a fallos:
    - Bucle de hasta N intentos con rotación estricta de proxy: NUNCA repite un proxy que ya falló.
    - Detección precisa de '100 consultas diarias' (blacklist permanente) vs '5 consultas por minuto' (cooldown).
    - Invalida el proxy agotado de inmediato en el adapter.
    - Únicamente retorna un resultado si ENACOM certifica COINCIDENCIA con prestador válido.
    - Si no logra certificar el número tras max_intentos con proxies distintos, retorna None para preservarlo 'pendiente' en SQLite.
    """
    linea = Linea(ani=ani)
    t_inicio = time.time()
    proxies_intentados: Set[str] = set()

    adapter = obtener_adapter_worker(proxy_manager)
    if not adapter:
        return None

    for intento in range(1, max_intentos + 1):
        if stop_requested.is_set():
            return None

        # Asegurar que el adapter tenga un proxy válido y NO probado previamente para esta línea
        p_actual = getattr(adapter, "proxy", None)
        blocked_proxies = proxy_manager.get_blocked_proxies() if proxy_manager else set()
        
        necesita_rotacion = (
            (proxy_manager is not None and not p_actual) or
            (p_actual in proxies_intentados) or
            (p_actual in blocked_proxies)
        )

        if necesita_rotacion and proxy_manager:
            nuevo_proxy = None
            log_espera_proxy = 0.0
            while not stop_requested.is_set():
                nuevo_proxy = proxy_manager.obtener_proxy_vivo(timeout=5.0, exclude=proxies_intentados)
                if nuevo_proxy:
                    break
                ahora_wait = time.time()
                if ahora_wait - log_espera_proxy >= 10.0:
                    log_espera_proxy = ahora_wait
                    logger.info(
                        f"⏳ ANI {ani}: En espera de nuevo proxy argentino validado para reintento #{intento} "
                        f"(descartando {len(proxies_intentados)} IPs probadas)..."
                    )
                time.sleep(2.0)

            if stop_requested.is_set() or not nuevo_proxy:
                return None

            adapter.rotar_proxy(nuevo_proxy)
            p_actual = nuevo_proxy

        # Registrar este proxy como intentado para esta línea específica
        if p_actual:
            proxies_intentados.add(p_actual)

        # Cadencia protegida por proxy (8.5 segundos + max 4/min)
        IPPacer.esperar_turno(p_actual, min_interval_sec=8.5)

        t0 = time.time()
        try:
            resultado = adapter.consultar_linea(linea)
            latencia = round(time.time() - t0, 3)

            # ÉXITO ROTUNDO: ENACOM identificó el prestador
            if resultado.status == StatusScraping.COINCIDENCIA and resultado.operador:
                detalles = resultado.detalles if isinstance(resultado.detalles, dict) else {}
                prestador_act = detalles.get("prestador_actual") or detalles.get("prestador_original") or resultado.operador
                orig = detalles.get("prestador_original") or resultado.operador
                es_portado = detalles.get("es_portado", False)

                desc_str = f"ENACOM: {prestador_act}"
                if es_portado:
                    desc_str += f" (Portado desde {orig})"

                # Preparar enriquecimiento de datos acumulativos
                datos_actualizados = dict(datos_previos) if isinstance(datos_previos, dict) else {}
                ns_enacom = resultado.to_namespace_dict()
                datos_actualizados.update(ns_enacom)

                # Construir lista acumulativa de fuentes
                fuente_actual = datos_actualizados.get("fuente") or []
                if isinstance(fuente_actual, str):
                    try:
                        fuente_actual = json.loads(fuente_actual)
                    except Exception:
                        fuente_actual = [fuente_actual]
                if not isinstance(fuente_actual, list):
                    fuente_actual = []
                if "enacom_web" not in fuente_actual:
                    fuente_actual.append("enacom_web")

                return {
                    "id": reg_id,
                    "ani": ani,
                    "status": "completado",
                    "scraper_actual": "finalizado",
                    "fuente": fuente_actual,
                    "datos": datos_actualizados,
                    "descripcion": desc_str[:200],
                    "latencia": latencia,
                    "intentos": intento,
                    "tiempo_total": round(time.time() - t_inicio, 2)
                }

            # Si el resultado fue ERROR o límite de tasa
            desc_baja = (resultado.descripcion or "").lower()
            detalles_err = resultado.detalles if isinstance(resultado.detalles, dict) else {}
            tipo_err = detalles_err.get("error_tipo", "")

            es_limite_diario = ("100" in desc_baja or "diaria" in desc_baja or tipo_err == "limite_diario")
            es_limite_minuto = ("5" in desc_baja or "minuto" in desc_baja or tipo_err == "limite_minuto")
            es_seguridad = ("seguridad" in desc_baja or "captcha" in desc_baja or tipo_err == "seguridad_recaptcha")
            es_fallo_ip = es_limite_diario or es_limite_minuto or es_seguridad or resultado.status == StatusScraping.ERROR

            if es_fallo_ip:
                if proxy_manager and p_actual:
                    if es_limite_diario:
                        proxy_manager.report_daily_limit(p_actual)
                    elif es_limite_minuto:
                        proxy_manager.report_minute_limit(p_actual, cooldown_sec=65.0)
                    else:
                        proxy_manager.report_proxy_result(p_actual, False, reason=resultado.descripcion)
                    
                    # Invalida inmediatamente el proxy en el adapter para forzar cambio de IP en el siguiente intento
                    adapter.invalidar_proxy()
                elif not proxy_manager and (es_limite_diario or es_limite_minuto):
                    logger.error(
                        f"🛑 [LÍMITE ALCANZADO EN IP DIRECTA] ENACOM informó: '{resultado.descripcion}'. "
                        f"Se detiene la ejecución para preservar la cola en SQLite. Rotá de IP pública o usá proxies."
                    )
                    stop_requested.set()
                    return None

            logger.warning(
                f"⚠️  [REINTENTO {intento}/{max_intentos}] ANI {ani}: {resultado.descripcion}."
            )

        except Exception as e:
            logger.warning(f"⚠️  [EXCEPCIÓN {intento}/{max_intentos}] ANI {ani}: {e}.")
            if proxy_manager and p_actual:
                proxy_manager.report_proxy_result(p_actual, False, reason=f"Exception: {e}")
                adapter.invalidar_proxy()

    # Si se agotaron los intentos sin confirmación absoluta: NO subir basura ni marcar fallido
    logger.error(f"❌ ANI {ani} no pudo ser certificado tras {max_intentos} intentos. Se preserva como 'pendiente' en SQLite.")
    return None


def push_a_vps(push_uc: SincronizarPushNocturnoUseCase, forzar: bool = False) -> int:
    """
    Sube al VPS utilizando el semáforo distribuido estándar MySQL (GET_LOCK/RELEASE_LOCK)
    en chunks de hasta 5.000 registros, subiendo también los remanentes menores a 5.000 sin esperar.

    REGLA ESTRICTA DE HORARIO:
    La subida masiva al VPS ÚNICAMENTE se ejecuta dentro de la ventana nocturna oficial (00:00 a 08:00 hs).
    Fuera de ese horario, los registros procesados se acumulan de forma segura en SQLite local (staging_local.db).
    """
    if not forzar and not config.esta_en_ventana_push():
        from datetime import datetime
        hora_act = datetime.now().strftime("%H:%M:%S")
        logger.info(
            f"🌙 [VENTANA PUSH CERRADA ({hora_act})] Fuera de ventana nocturna (00:00 a 08:00 hs). "
            f"Los registros confirmados permanecen seguros en SQLite local (data/staging_local.db)."
        )
        return 0

    def should_stop() -> bool:
        if stop_requested.is_set():
            return True
        if not forzar and not config.esta_en_ventana_push():
            logger.info("⏰ [FIN DE VENTANA 08:00 hs] Interrumpiendo push nocturno. Remanentes para el próximo ciclo.")
            return True
        return False

    res = push_uc.ejecutar(
        tipo_cola="registro_no_llame",
        chunk_size=5000,
        sweep_wait_sec=0.5,
        dias_retencion=7,
        roundrobin_pause_sec=1.5,
        should_stop=should_stop
    )
    return res.get("total_subidos", 0)


def main():
    parser = argparse.ArgumentParser(description="Procesador Industrial ENACOM Web - Registro No Llame (Staging 5.000 / Anti-Errores)")
    parser.add_argument("--workers", type=int, default=5, help="Cantidad de workers/navegadores concurrentes (default: 5)")
    parser.add_argument("--batch-size", type=int, default=100, help="Tamaño de micro-lote por ciclo de trabajo (default: 100)")
    parser.add_argument("--pull-limit", type=int, default=5000, help="Líneas a descargar del VPS por bloque masivo (default: 5000)")
    parser.add_argument("--watermark-min", type=int, default=1000, help="Umbral mínimo en SQLite antes de disparar pull de recarga (default: 1000)")
    parser.add_argument("--push-interval", type=int, default=500, help="Cada cuántas consultas confirmadas evaluar push al VPS (default: 500)")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy único a usar prioritariamente (ej: http://ip:puerto o socks5://ip:puerto)")
    parser.add_argument("--proxy-file", type=str, default=None, help="Ruta a archivo .txt con lista de proxies (uno por línea)")
    parser.add_argument("--no-proxy", action="store_true", help="Forzar salida por IP local directa (solo testing)")
    parser.add_argument("--force-push", action="store_true", help="Forzar subida al VPS ignorando la ventana nocturna 00:00-08:00 (solo testing)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=" * 80)
    print("🚀 MOTOR INDUSTRIAL ENACOM WEB - REGISTRO NO LLAME")
    print("Arquitectura: SQLite Staging 5.000 en WAL | Cero Errores | Watermark Pull Automático")
    print(f"Configuración: {args.workers} Workers | Micro-Lote: {args.batch_size} | Recarga VPS: 5.000 (Umbral < {args.watermark_min})")
    print("=" * 80)

    # 1. Repositorios Hexagonales
    sqlite_repo = SQLiteStagingAdapter(tipo_cola="registro_no_llame")
    vps_sync = VPSSyncAdapter()
    pull_uc = SincronizarPullMatutinoUseCase(remote_repo=vps_sync, local_repo=sqlite_repo)
    push_uc = SincronizarPushNocturnoUseCase(remote_repo=vps_sync, local_repo=sqlite_repo)

    # 2. Configuración de Harvester de Proxies de Argentina (MODO POR DEFECTO: ESCUDO IP)
    proxy_manager = None
    if args.no_proxy:
        logger.warning("⚠️  [MODO DIRECTO FORZADO] --no-proxy activado: Las peticiones saldrán por tu IP local.")
    else:
        logger.info("🛡️  [ESCUDO IP ACTIVO] Harvester de proxies de Argentina iniciado. 0 consultas saldrán por tu IP local.")
        proxy_manager = BurstProxyManager.get_instance()
        proxy_manager.start()

        if args.proxy:
            proxy_manager.set_static_proxy(args.proxy)
        elif args.proxy_file:
            if os.path.exists(args.proxy_file):
                with open(args.proxy_file, "r", encoding="utf-8", errors="ignore") as pf:
                    lineas = [l.strip() for l in pf if l.strip()]
                    proxy_manager.load_custom_proxies(lineas)
            else:
                logger.error(f"Archivo de proxies no encontrado: {args.proxy_file}")

    # 3. Pool de workers concurrentes (instanciación thread-local bajo demanda)
    logger.info(f"Pool de {args.workers} workers concurrentes inicializado (motores Chromium on-demand por hilo).")

    total_confirmadas = 0
    desde_ultimo_push = 0

    try:
        while not stop_requested.is_set():
            # A. Control de Watermark: Comprobar nivel de agua en SQLite Local para enacom_web
            pendientes = sqlite_repo.contar_pendientes(tipo_cola="registro_no_llame", scraper_actual="enacom_web")
            logger.info(f"💧 [WATERMARK STATUS] Tareas pendientes en SQLite local (enacom_web): {pendientes:,} / {args.pull_limit:,}")

            if pendientes < args.watermark_min:
                logger.info(
                    f"🔄 [WATERMARK PULL ACTIVADO] Pendientes locales ({pendientes:,}) < Umbral ({args.watermark_min:,}). "
                    f"Descargando bloque de {args.pull_limit:,} líneas del VPS..."
                )
                res_pull = pull_uc.ejecutar(
                    tipo_cola="registro_no_llame",
                    limit=args.pull_limit,
                    umbral_minimo=args.watermark_min,
                    scraper_actual="enacom_web"
                )
                pendientes = sqlite_repo.contar_pendientes(tipo_cola="registro_no_llame", scraper_actual="enacom_web")
                if res_pull.get("descargados", 0) == 0 and pendientes == 0:
                    logger.warning("🏁 No quedan líneas con 'no_coincidencia' disponibles en el VPS central. Finalizando.")
                    break

            # B. Reclamar micro-lote local a 0 ms de latencia
            registros = sqlite_repo.reservar_lote(
                batch_size=args.batch_size,
                scraper_nombre="enacom_web"
            )
            if not registros:
                pendientes = sqlite_repo.contar_pendientes(tipo_cola="registro_no_llame", scraper_actual="enacom_web")
                if pendientes > 0:
                    time.sleep(1.0)
                    continue
                else:
                    logger.info("No se pudieron reservar más tareas y pendientes es 0. Reevaluando...")
                    continue

            # C. Estado del pool de proxies
            if proxy_manager:
                q_len = proxy_manager.ready_queue.qsize()
                logger.info(f"🌐 [POOL PROXIES] Disponibles en cola: {q_len} | Consultas ejecutadas: {proxy_manager.stats.get('queries_executed', 0)}")

            # D. Procesar concurrentemente con reintentos y tolerancia a fallos
            logger.info(f"⚡ Disparando micro-lote de {len(registros)} líneas sobre {args.workers} workers...")
            resultados_exitosos = []
            ids_a_revertir = []

            executor = ThreadPoolExecutor(max_workers=min(args.workers, len(registros)))
            futures = {}
            try:
                for reg in registros:
                    if stop_requested.is_set():
                        ids_a_revertir.append(reg.id)
                        continue
                    fut = executor.submit(
                        procesar_registro_robusto,
                        proxy_manager,
                        reg.id,
                        reg.linea.ani,
                        reg.datos_existentes,
                        max_intentos=2 if not proxy_manager else 4
                    )
                    futures[fut] = reg.id

                for f in as_completed(futures):
                    if stop_requested.is_set():
                        for pending_fut in futures:
                            pending_fut.cancel()
                        break
                    reg_id = futures[f]
                    try:
                        res = f.result()
                        if res:
                            # Persistencia streaming inmediata en SQLite (cero pérdida de datos ante interrupción)
                            sqlite_repo.persistir_resultados([res])
                            total_confirmadas += 1
                            desde_ultimo_push += 1
                            print(f"  ✅ [CONFIRMADO] ANI: {res['ani']} | {res['descripcion']} (Lat: {res['latencia']}s | Intento: #{res['intentos']})")
                        else:
                            ids_a_revertir.append(reg_id)
                    except Exception as exc:
                        logger.warning(f"Error en worker para registro {reg_id}: {exc}")
                        ids_a_revertir.append(reg_id)

                if stop_requested.is_set():
                    for fut, reg_id in futures.items():
                        if not fut.done() or fut.cancelled():
                            if reg_id not in ids_a_revertir:
                                ids_a_revertir.append(reg_id)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # Revertir limpiamente los casos transitorios a 'pendiente' para reintento futuro
            if ids_a_revertir:
                sqlite_repo.revertir_a_pendiente(ids_a_revertir)
                logger.info(f"↩️  {len(ids_a_revertir)} líneas revertidas a 'pendiente' en SQLite para nuevo intento con proxy fresco.")

            # G. Evaluar Push masivo hacia MySQL VPS (solo en ventana 00:00 a 08:00 hs o forzado)
            if desde_ultimo_push >= args.push_interval:
                push_a_vps(push_uc, forzar=args.force_push)
                desde_ultimo_push = 0

    finally:
        # Cierre seguro, purgado y push final
        logger.info("\n🛑 Cerrando workers de Chromium y deteniendo Harvester...")
        if proxy_manager:
            proxy_manager.stop()
        cerrar_todos_los_adapters()

        # Push final de todos los registros completados que queden en SQLite (respeta ventana 00:00 - 08:00)
        push_a_vps(push_uc, forzar=args.force_push)
        stats = sqlite_repo.obtener_estadisticas()
        print("\n" + "=" * 80)
        print("📊 RESUMEN FINAL DE PRODUCCIÓN")
        print("=" * 80)
        print(f"Total de líneas confirmadas y consolidadas en esta sesión: {total_confirmadas:,}")
        print(f"Estado en SQLite Local: Pendientes: {stats.get('pendiente', 0):,} | Sincronizados: {stats.get('sincronizado', 0):,}")
        print("=" * 80)


if __name__ == "__main__":
    main()
