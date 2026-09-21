# -*- coding: utf-8 -*-
"""
Paquete de Adaptadores de Red e Infraestructura de Conectividad.
"""
from adapters.network.tor_controller import TorController
from adapters.network.proxy_pool import ProxyPoolManager

__all__ = ["TorController", "ProxyPoolManager"]
