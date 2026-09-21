# -*- coding: utf-8 -*-
"""
Puerto de Salida (Secondary / Driven Port): Contrato de Cola y Persistencia
Define la interfaz abstracta para interactuar con la cola de procesamiento.
Cualquier base de datos (MySQL, Postgres, SQLite, Redis o Memory) debe implementar este contrato.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from core.domain.entities import RegistroCola

class IColaRepositorioPort(ABC):
    """Contrato abstracto para el repositorio de colas y persistencia."""

    @abstractmethod
    def reservar_lote(
        self, 
        batch_size: int = 15, 
        prioridad: Optional[int] = None, 
        scraper_nombre: str = "iris",
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        """
        Reclama atómicamente un micro-lote de registros para el scraper especificado.
        Debe aplicar bloqueo de concurrencia (ej: SKIP LOCKED), priorización (P1 -> P2 -> P3)
        y opcionalmente filtrar solo registros sin coincidencias telco previas (solo_sin_coincidencia).
        """
        pass

    @abstractmethod
    def persistir_resultados(self, resultados: List[Dict[str, Any]]) -> bool:
        """
        Actualiza en lote los registros procesados:
        Avanza la etapa (scraper_actual), actualiza estado, anota fuente acumulativa
        y fusiona datos_json bajo el namespace correspondiente.
        """
        pass

    @abstractmethod
    def revertir_a_pendiente(self, ids: List[int]) -> bool:
        """
        Devuelve una lista de IDs del estado 'procesando' al estado 'pendiente'.
        Crucial para recuperaciones ante apagado ordenado o fallos de workers.
        """
        pass

    @abstractmethod
    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        """
        Watchdog Sweeper: Recupera registros colgados en 'procesando' tras caídas imprevistas.
        """
        pass

    @abstractmethod
    def obtener_estadisticas(self) -> Dict[str, int]:
        """
        Devuelve el conteo de registros desglosado por scraper_actual y estado.
        """
        pass
