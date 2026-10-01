# -*- coding: utf-8 -*-
"""
Puerto Secundario (Secondary / Driven Port): Contrato de Sincronización Staging <-> VPS
Arquitectura Hexagonal - Puertos para sincronización matutina (Pull) y nocturna (Push)

Aisla completamente las llamadas remotas de MySQL de la lógica de negocio de los casos de uso.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class ISyncRemoteRepoPort(ABC):
    """Contrato abstracto para operaciones en lote contra el VPS central (MySQL)."""

    @abstractmethod
    def descargar_lote_vps(
        self,
        tipo_cola: str,
        limit: int = 10000,
        auto_id: Optional[str] = None,
        pc_id: Optional[str] = None,
        scraper_actual: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Reclama atómicamente un bloque masivo de tareas en estado 'pendiente' en el VPS
        utilizando SELECT ... FOR UPDATE SKIP LOCKED y las pasa atómicamente a 'en_proceso' (o 'procesando').
        """
        pass

    @abstractmethod
    def subir_lote_vps(
        self,
        tipo_cola: str,
        lote: List[Dict[str, Any]]
    ) -> int:
        """
        Sube un lote (hasta 5.000 filas) al VPS ejecutando un solo UPDATE masivo (executemany)
        dentro de una transacción atómica aislada. Retorna la cantidad de filas actualizadas.
        """
        pass


class ISyncLocalRepoPort(ABC):
    """Contrato abstracto para el almacén local de staging (SQLite WAL)."""

    @abstractmethod
    def insertar_tareas_descargadas(
        self,
        tareas: List[Dict[str, Any]],
        tipo_cola: Optional[str] = None
    ) -> int:
        """Inserta en bloque las tareas descargadas del VPS con estado 'pendiente'."""
        pass

    @abstractmethod
    def obtener_lote_para_push(
        self,
        limit: int = 5000,
        tipo_cola: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Obtiene un lote de registros terminados ('listo_para_subir' o 'fallido') esperando subida."""
        pass

    @abstractmethod
    def marcar_como_sincronizados(
        self,
        ids_vps: List[int],
        tipo_cola: Optional[str] = None
    ) -> bool:
        """Marca las tareas como 'sincronizado' y anota fecha_sincronizado = CURRENT_TIMESTAMP."""
        pass

    @abstractmethod
    def purgar_antiguos(
        self,
        dias_retencion: int = 7
    ) -> int:
        """Elimina de SQLite registros sincronizados con más de N días de antigüedad."""
        pass

    @abstractmethod
    def contar_pendientes(
        self,
        tipo_cola: Optional[str] = None
    ) -> int:
        """Retorna la cantidad actual de registros 'pendiente' en la cola local."""
        pass
