# -*- coding: utf-8 -*-
"""
Puerto de Salida (Secondary / Driven Port): Contrato de Asignación de Bloques de Numeración
Define la interfaz abstracta para resolver la procedencia técnica, geográfica y regulatoria
oficial de un número telefónico (ENACOM / Plan Fundamental de Numeración de Argentina).
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from core.domain.entities import DatosOrigenEnacom

class IOperatorLookupPort(ABC):
    """Contrato abstracto para el motor de asignación de numeración y operador de origen."""

    @abstractmethod
    def consultar_bloque(self, ani: str) -> Optional[DatosOrigenEnacom]:
        """
        Resuelve un ANI normalizado contra el mapa oficial de bloques telefónicos.
        Retorna la entidad de dominio DatosOrigenEnacom o None si el prefijo es inválido.
        """
        pass

    @abstractmethod
    def obtener_total_bloques(self) -> int:
        """Retorna la cantidad total de bloques de numeración cargados en el motor."""
        pass

    @abstractmethod
    def esta_listo(self) -> bool:
        """Verifica si el índice de bloques se encuentra disponible en memoria para consultas."""
        pass
