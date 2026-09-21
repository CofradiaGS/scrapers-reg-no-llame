# -*- coding: utf-8 -*-
"""
Clase Base Abstracta para Adaptadores de Scrapers.
Proporciona utilidades comunes de jitter y formateo.
"""
import random
import time
from core.ports.scraper_port import IScraperEnginePort

class BaseScraperAdapter(IScraperEnginePort):
    """Clase base reutilizable con utilidades para scrapers."""

    def sleep_jitter(self, delay_min: float = 1.5, delay_max: float = 2.5) -> None:
        """Pausa con aleatoriedad para emular comportamiento humano o proteger rate-limits."""
        pause = random.uniform(delay_min, delay_max)
        time.sleep(pause)
