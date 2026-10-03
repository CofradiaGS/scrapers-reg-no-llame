# -*- coding: utf-8 -*-
"""
Caso de Uso de Aplicación: Sincronización Matutina / Recarga Watermark (Pull)
Arquitectura Hexagonal - Orquestador Staging Local <-> MySQL VPS

Evalúa el nivel de agua (watermark) de tareas pendientes en SQLite local.
Si desciende por debajo del umbral mínimo (default: 2.000 tareas),
solicita atómicamente un bloque (default: 10.000 tareas) al VPS central
usando FOR UPDATE SKIP LOCKED y las inyecta en el disco local SSD.
"""
import logging
from typing import Dict, Any, Optional
from core.ports.sync_port import ISyncRemoteRepoPort, ISyncLocalRepoPort

logger = logging.getLogger("SyncPullUseCase")


class SincronizarPullMatutinoUseCase:
    """Caso de uso para reabastecimiento atómico de la cola local desde el VPS."""

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
        limit: int = 10000,
        forzar: bool = False,
        umbral_minimo: int = 2000,
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        scraper_actual: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta la evaluación de watermark y eventual pull desde el VPS.

        :param tipo_cola: 'cola_automatizacion' o 'registro_no_llame'.
        :param limit: Cantidad de tareas a solicitar al VPS en 1 sola consulta atómica.
        :param forzar: Si True, ignora el umbral y descarga inmediatamente.
        :param umbral_minimo: Si los pendientes locales están por debajo de este valor, se dispara el pull.
        :param auto_id: Identificador de automatización para cola_automatizacion.
        :param pc_id: Identificador del nodo/PC local.
        :param scraper_actual: Etapa requerida para queue_registro_no_llame.
        :return: Resumen de la operación.
        """
        pendientes = self.local.contar_pendientes(tipo_cola=tipo_cola)

        if not forzar and pendientes > umbral_minimo:
            logger.debug(
                f"💧 [WATERMARK OK] Staging local tiene {pendientes:,} pendientes "
                f"(Umbral: {umbral_minimo:,}). No se requiere pull de recarga."
            )
            return {
                "ejecutado": False,
                "motivo": "buffer_suficiente",
                "pendientes_locales": pendientes,
                "descargados": 0,
                "insertados": 0
            }

        logger.info(
            f"🔄 [WATERMARK PULL] Disparando recarga de staging local ({tipo_cola}). "
            f"Pendientes actuales: {pendientes:,} <= Umbral: {umbral_minimo:,} (o forzado={forzar}). "
            f"Solicitando bloque de {limit:,} tareas al VPS..."
        )

        try:
            tareas = self.remote.descargar_lote_vps(
                tipo_cola=tipo_cola,
                limit=limit,
                auto_id=auto_id,
                pc_id=pc_id,
                scraper_actual=scraper_actual
            )

            insertadas = 0
            if tareas:
                insertadas = self.local.insertar_tareas_descargadas(tareas, tipo_cola=tipo_cola)
                logger.info(
                    f"✅ [WATERMARK PULL EXITOSO] {len(tareas):,} tareas descargadas del VPS, "
                    f"{insertadas:,} insertadas en SQLite local."
                )
            else:
                logger.warning(
                    f"⚠️ [WATERMARK PULL VACÍO] No hay tareas pendientes disponibles en el VPS "
                    f"para tipo_cola='{tipo_cola}'."
                )

            total_ahora = self.local.contar_pendientes(tipo_cola=tipo_cola)
            return {
                "ejecutado": True,
                "descargados": len(tareas),
                "insertados": insertadas,
                "pendientes_locales": total_ahora
            }

        except Exception as e:
            logger.error(f"❌ Error durante el pull matutino/recarga desde el VPS: {e}", exc_info=True)
            return {
                "ejecutado": False,
                "error": str(e),
                "pendientes_locales": pendientes,
                "descargados": 0,
                "insertados": 0
            }
