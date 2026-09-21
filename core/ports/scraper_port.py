# -*- coding: utf-8 -*-
"""
Puerto de Salida (Secondary / Driven Port): Contrato de Motores de Scraping
Cualquier scraper (IRIS HTTP, IRIS Playwright, Claro, Personal, Renaper, etc.)
debe implementar este contrato para enchufarse al pipeline.
"""
from abc import ABC, abstractmethod
from core.domain.entities import Linea, ScrapeResult

class IScraperEnginePort(ABC):
    """Contrato abstracto para cualquier motor de extracción externa."""

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador único del scraper (ej: 'iris', 'claro', 'personal')."""
        pass

    @abstractmethod
    def iniciar(self) -> None:
        """Inicializa recursos subyacentes (cliente HTTP, navegador, pools, etc.)."""
        pass

    @abstractmethod
    def autenticar(self) -> bool:
        """Realiza login o validación de tokens/sesión con el portal remoto."""
        pass

    @abstractmethod
    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta una línea telefónica individual y devuelve el resultado normalizado
        bajo el modelo de dominio ScrapeResult.
        """
        pass

    @abstractmethod
    def verificar_salud(self) -> bool:
        """
        Health-check rápido para el Circuit Breaker (ej: ping al gateway/VPN).
        Retorna True si el portal remoto responde normalmente.
        """
        pass

    @abstractmethod
    def cerrar(self) -> None:
        """Cierra sesiones, clientes y libera el 100% de los recursos en memoria."""
        pass
