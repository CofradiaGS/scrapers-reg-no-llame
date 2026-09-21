# -*- coding: utf-8 -*-
"""
Enums de Dominio - Arquitectura Hexagonal
Definiciones inmutables de estados, prioridades y resultados de scraping.
"""
from enum import Enum, IntEnum

class Prioridad(IntEnum):
    """
    Niveles de prioridad de consumo de líneas telefónicas.
    P1: AMBA (11) y Mendoza completa
    P2: Sur / Patagonia
    P3: Resto del país
    """
    P1_AMBA_MENDOZA = 1
    P2_SUR_PATAGONIA = 2
    P3_RESTO = 3

    @classmethod
    def from_int(cls, value: int) -> "Prioridad":
        for p in cls:
            if p.value == value:
                return p
        return cls.P3_RESTO

    @property
    def descripcion(self) -> str:
        desc = {
            1: "P1 (11 / Mendoza)",
            2: "P2 (Sur / Patagonia)",
            3: "P3 (Resto del País)"
        }
        return desc.get(self.value, "Desconocida")


class EstadoRegistro(str, Enum):
    """Estados del ciclo de vida de una línea en cola de procesamiento."""
    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    COMPLETADO = "completado"
    NO_COINCIDENCIA = "no_coincidencia"
    ERROR = "error"


class StatusScraping(str, Enum):
    """Resultado normalizado de una consulta en cualquier scraper."""
    COINCIDENCIA = "coincidencia"
    SIN_COINCIDENCIA = "sin_coincidencia"
    ERROR = "error"
