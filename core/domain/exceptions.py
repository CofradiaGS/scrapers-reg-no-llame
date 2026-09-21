# -*- coding: utf-8 -*-
"""
Excepciones de Dominio - Arquitectura Hexagonal
Permite a la aplicación capturar y manejar errores tipados sin acoplarse a librerías externas.
"""

class DomainException(Exception):
    """Excepción base para todos los errores de dominio."""
    pass

class QueueEmptyException(DomainException):
    """Lanzada cuando no hay registros pendientes disponibles en la cola."""
    pass

class QueueConnectionError(DomainException):
    """Lanzada cuando la conexión a la infraestructura de colas falla."""
    pass

class ScraperException(DomainException):
    """Excepción base para fallos originados en un motor de scraping."""
    pass

class ScraperAuthenticationError(ScraperException):
    """Lanzada cuando falla el login o expira la sesión del scraper."""
    pass

class ScraperTransientError(ScraperException):
    """Lanzada ante fallos transitorios de red, timeout o rate limit."""
    pass

class ScraperFatalError(ScraperException):
    """Lanzada ante errores irrecuperables (pantalla bloqueada, cuenta suspendida, etc.)."""
    pass

class FueraDeHorarioComercialException(ScraperException):
    """Lanzada cuando se intenta ejecutar una consulta a un scraper fuera de su ventana comercial permitida."""
    pass
