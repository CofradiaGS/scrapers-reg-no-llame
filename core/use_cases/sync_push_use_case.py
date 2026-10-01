# -*- coding: utf-8 -*-
"""
Caso de Uso de Aplicación: Sincronización Nocturna Masiva (Push 20:00 hs)
Arquitectura Hexagonal - Orquestador Staging Local <-> MySQL VPS

Sube los resultados procesados en disco local SSD al VPS central:
- Procesa en chunks de hasta 5.000 registros por transacción atómica.
- Soporta lotes remanentes parciales sin esperar a que se completen los 5.000.
- Ejecuta una pasada de barrido final (Sweep) para capturar tareas terminadas durante el push.
- Ejecuta la purga rotativa de tareas sincronizadas con más de 7 días de antigüedad.
"""
import time
import logging
from typing import Dict, Any, Optional
from core.ports.sync_port import ISyncRemoteRepoPort, ISyncLocalRepoPort

logger = logging.getLogger("SyncPushUseCase")


class SincronizarPushNocturnoUseCase:
    """Caso de uso para subida masiva nocturna atómica en chunks de a 5.000 al VPS."""

    def __init__(
        self,
        remote_repo: ISyncRemoteRepoPort,
        local_repo: ISyncLocalRepoPort
    ):
        self.remote = remote_repo
        self.local = local_repo

    def ejecutar(
        self,
        tipo_cola: str = "cola_automatizacion",
        chunk_size: int = 5000,
        sweep_wait_sec: float = 10.0,
        dias_retencion: int = 7,
        roundrobin_pause_sec: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta el push masivo al VPS.

        :param tipo_cola: 'cola_automatizacion' o 'registro_no_llame'.
        :param chunk_size: Cantidad máxima de filas por transacción UPDATE (5.000).
        :param sweep_wait_sec: Pausa previa al barrido final de remanentes.
        :param dias_retencion: Días para la purga rotativa en SQLite local (7 días).
        :return: Resumen de sincronización.
        """
        t0 = time.time()
        try:
            import config
            default_pause = getattr(config, "SYNC_PUSH_ROUNDROBIN_PAUSE_SEC", 2.0)
        except Exception:
            default_pause = 2.0

        roundrobin_pause = default_pause if roundrobin_pause_sec is None else roundrobin_pause_sec

        logger.info(
            f"🚀 [PUSH NOCTURNO] Iniciando subida masiva a VPS ({tipo_cola}). "
            f"Chunk size: {chunk_size:,} | Retención local: {dias_retencion} días | Pausa Round-Robin: {roundrobin_pause}s."
        )

        total_subidos = 0
        lotes_procesados = 0
        lote_num = 1

        while True:
            lote = self.local.obtener_lote_para_push(limit=chunk_size, tipo_cola=tipo_cola)
            if not lote:
                break

            cantidad = len(lote)
            logger.info(
                f"📦 [PUSH LOTE {lote_num}] Enviando chunk de {cantidad:,} registros a MySQL VPS..."
            )

            try:
                self.remote.subir_lote_vps(tipo_cola=tipo_cola, lote=lote)
                ids_vps = [r["id_vps"] for r in lote]
                self.local.marcar_como_sincronizados(ids_vps=ids_vps, tipo_cola=tipo_cola)

                total_subidos += cantidad
                lotes_procesados += 1
                lote_num += 1
                logger.info(
                    f"✅ [PUSH LOTE {lote_num - 1} OK] {cantidad:,} registros comprometidos en VPS "
                    f"y marcados como 'sincronizado' en SQLite local."
                )

                # Pausa cooperativa Round-Robin: ceder turno a otras PCs en la red
                if roundrobin_pause > 0:
                    logger.info(
                        f"⏸️ [ROUND-ROBIN] Pausa cooperativa de {roundrobin_pause}s para ceder turno a otras PCs en red..."
                    )
                    time.sleep(roundrobin_pause)

            except Exception as e:
                logger.error(f"❌ [PUSH LOTE {lote_num} ERROR] Falló subida al VPS: {e}", exc_info=True)
                return {
                    "exito": False,
                    "error": str(e),
                    "total_subidos": total_subidos,
                    "lotes_procesados": lotes_procesados,
                    "duracion_segundos": round(time.time() - t0, 2)
                }

        # Barrido Final (Sweep) para capturar cualquier registro finalizado durante la subida
        remanentes_sweep = 0
        if sweep_wait_sec > 0:
            logger.info(f"⏳ [PUSH SWEEP] Esperando {sweep_wait_sec}s para barrido final de remanentes...")
            time.sleep(sweep_wait_sec)

            lote_sweep = self.local.obtener_lote_para_push(limit=chunk_size, tipo_cola=tipo_cola)
            if lote_sweep:
                cant_sweep = len(lote_sweep)
                logger.info(f"🧹 [PUSH SWEEP] Capturados {cant_sweep:,} registros remanentes. Subiendo...")
                try:
                    self.remote.subir_lote_vps(tipo_cola=tipo_cola, lote=lote_sweep)
                    ids_sweep = [r["id_vps"] for r in lote_sweep]
                    self.local.marcar_como_sincronizados(ids_vps=ids_sweep, tipo_cola=tipo_cola)
                    total_subidos += cant_sweep
                    lotes_procesados += 1
                    remanentes_sweep = cant_sweep
                    logger.info(f"✅ [PUSH SWEEP OK] {cant_sweep:,} remanentes sincronizados con éxito.")
                except Exception as e_sweep:
                    logger.error(f"⚠️ [PUSH SWEEP ERROR] Falló el barrido final: {e_sweep}")

        # Purga rotativa de tareas sincronizadas con más de N días
        purgados = 0
        try:
            purgados = self.local.purgar_antiguos(dias_retencion=dias_retencion)
        except Exception as e_purga:
            logger.warning(f"Aviso durante la purga rotativa: {e_purga}")

        duracion = round(time.time() - t0, 2)
        logger.info(
            f"🏁 [PUSH NOCTURNO COMPLETADO] Total sincronizados: {total_subidos:,} en {lotes_procesados} transacciones. "
            f"Remanentes sweep: {remanentes_sweep:,} | Purgados > {dias_retencion}d: {purgados:,} | Tiempo: {duracion}s."
        )

        return {
            "exito": True,
            "total_subidos": total_subidos,
            "lotes_procesados": lotes_procesados,
            "remanentes_sweep": remanentes_sweep,
            "purgados": purgados,
            "duracion_segundos": duracion
        }
