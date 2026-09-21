# -*- coding: utf-8 -*-
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.scrapers.registry import ScraperRegistry
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from adapters.scrapers.iris.iris_browser_adapter import IrisBrowserAdapter

__all__ = [
    "BaseScraperAdapter",
    "ScraperRegistry",
    "IrisHttpAdapter",
    "IrisBrowserAdapter"
]
