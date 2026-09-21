# -*- coding: utf-8 -*-
"""
Módulo del Scraper IRIS (Movistar BPM).
Contiene los motores de automatización (HTTP y Browser), el parser de portabilidad y los adaptadores hexagonales.
"""
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from adapters.scrapers.iris.iris_browser_adapter import IrisBrowserAdapter
from adapters.scrapers.iris.iris_http_bot import IrisHttpBot
from adapters.scrapers.iris.iris_bot import IrisBot
from adapters.scrapers.iris.parser import parse_iris_detail

__all__ = [
    "IrisHttpAdapter",
    "IrisBrowserAdapter",
    "IrisHttpBot",
    "IrisBot",
    "parse_iris_detail"
]
