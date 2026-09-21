# -*- coding: utf-8 -*-
"""
Registro y Factoría Central de Scrapers.
Permite instanciar y registrar scrapers por nombre dinámicamente:
--scraper iris_http
--scraper iris_browser
--scraper claro
"""
from typing import Dict, Type
from core.ports.scraper_port import IScraperEnginePort
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from adapters.scrapers.iris.iris_browser_adapter import IrisBrowserAdapter
from adapters.scrapers.claro.claro_adapter import ClaroAdapter
from adapters.scrapers.movistar.movistar_adapter import MovistarAdapter
from adapters.scrapers.personal.personal_adapter import PersonalAdapter
from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter
from adapters.scrapers.cuitonline.cuitonline_adapter import CuitOnlineAdapter

class ScraperRegistry:
    _registry: Dict[str, Type[IScraperEnginePort]] = {
        "iris_http": IrisHttpAdapter,
        "iris": IrisHttpAdapter,          # Alias por defecto
        "iris_browser": IrisBrowserAdapter,
        "datuar": DatuarAdapter,
        "cuitonline": CuitOnlineAdapter,
        "cuit_online": CuitOnlineAdapter,
        "claro": ClaroAdapter,
        "claro_cobro_express": ClaroAdapter,
        "claro_fast": ClaroAdapter,
        "movistar": MovistarAdapter,
        "movistar_cobro_express": MovistarAdapter,
        "movistar_fast": MovistarAdapter,
        "personal": PersonalAdapter,
        "personal_cobro_express": PersonalAdapter,
        "personal_fast": PersonalAdapter,
    }

    @classmethod
    def registrar(cls, alias: str, scraper_cls: Type[IScraperEnginePort]) -> None:
        """Permite a nuevos scrapers registrarse dinámicamente."""
        cls._registry[alias.lower()] = scraper_cls

    @classmethod
    def obtener(cls, alias: str, **kwargs) -> IScraperEnginePort:
        """Instancia un scraper registrado por su alias."""
        alias_clean = alias.lower().strip()
        if alias_clean == "claro_fast":
            kwargs.setdefault("use_proxy_pool", True)
            kwargs["use_tor"] = False
            return ClaroAdapter(**kwargs)

        if alias_clean == "movistar_fast":
            kwargs.setdefault("use_proxy_pool", True)
            kwargs["use_tor"] = False
            return MovistarAdapter(**kwargs)

        if alias_clean == "personal_fast":
            kwargs.setdefault("use_proxy_pool", True)
            kwargs["use_tor"] = False
            return PersonalAdapter(**kwargs)

        scraper_cls = cls._registry.get(alias_clean)
        if not scraper_cls:
            disponibles = ", ".join(cls._registry.keys())
            raise ValueError(f"Scraper '{alias}' no encontrado en el registro. Disponibles: {disponibles}")

        import inspect
        sig = inspect.signature(scraper_cls.__init__)
        has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_var_kwargs:
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return scraper_cls(**filtered_kwargs)
        return scraper_cls(**kwargs)

    @classmethod
    def listar_disponibles(cls) -> list[str]:
        return list(cls._registry.keys())
