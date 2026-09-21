# -*- coding: utf-8 -*-
from core.ports.queue_port import IColaRepositorioPort
from core.ports.scraper_port import IScraperEnginePort
from core.ports.operator_lookup_port import IOperatorLookupPort

__all__ = ["IColaRepositorioPort", "IScraperEnginePort", "IOperatorLookupPort"]
