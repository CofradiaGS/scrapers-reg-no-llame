# -*- coding: utf-8 -*-
"""
PLANTILLA DE EJEMPLO: Cómo añadir un nuevo Scraper (Plug & Play)
Para agregar un nuevo scraper (ej: Claro, Personal, Renaper):
1. Copiá este archivo en adapters/scrapers/claro/claro_adapter.py
2. Rellená la lógica en consultar_linea y autenticar.
3. Registralo en ScraperRegistry.
Listo. Cero cambios en la base de datos ni en el supervisor.
"""
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import Linea, ScrapeResult, Titular
from core.domain.enums import StatusScraping

class TemplateScraperAdapter(IScraperEnginePort):
    def __init__(self):
        self._conectado = False

    @property
    def nombre(self) -> str:
        # El nombre del scraper en la cadena del pipeline (ej: 'claro', 'personal')
        return "nuevo_scraper"

    def iniciar(self) -> None:
        # Inicializar cliente HTTP o sesión
        self._conectado = True

    def autenticar(self) -> bool:
        # Login en el portal externo si aplica
        return True

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        # Aquí va la petición externa al portal o endpoint
        # Ejemplo de coincidencia simulada:
        encontrado = True

        if encontrado:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.COINCIDENCIA,
                fuente_scraper=self.nombre,
                operador="Compañía Ejemplo",
                titular=Titular(nombre="Nombre Titular", nro_documento="12345678"),
                descripcion=f"Coincidencia positiva en {self.nombre}"
            )
        else:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"Sin coincidencia en {self.nombre}"
            )

    def verificar_salud(self) -> bool:
        return self._conectado

    def cerrar(self) -> None:
        self._conectado = False
