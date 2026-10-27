# -*- coding: utf-8 -*-
"""
Script de Sincronización Inmediata al VPS para Staging Local.
Sube todos los registros en 'listo_para_subir' al VPS en chunks de a 5.000 registros,
pasándolos a 'completado' en MySQL y marcándolos como 'sincronizado' en SQLite.
"""
import sys
sys.path.insert(0, ".")
import logging
import config
from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter
from adapters.queue.vps_sync_adapter import VPSSyncAdapter
from core.use_cases.sync_push_use_case import SincronizarPushNocturnoUseCase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SincronizarVPS")

local_repo = SQLiteStagingAdapter(
    db_path=config.LOCAL_STAGING_DB_PATH,
    tipo_cola="cola_automatizacion"
)
remote_repo = VPSSyncAdapter(pool_size=4)
use_case = SincronizarPushNocturnoUseCase(remote_repo=remote_repo, local_repo=local_repo)

listos = local_repo.contar_listos_para_subir(tipo_cola="cola_automatizacion")
logger.info(f"Registros listos para subir antes de la sincronización: {listos:,}")

res = use_case.ejecutar(
    tipo_cola="cola_automatizacion",
    chunk_size=5000,
    roundrobin_pause_sec=0.1,
    exigir_lote_completo=False
)

logger.info(f"Resultado final del push: {res}")
listos_despues = local_repo.contar_listos_para_subir(tipo_cola="cola_automatizacion")
logger.info(f"Registros listos para subir después de la sincronización: {listos_despues:,}")
