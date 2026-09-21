# -*- coding: utf-8 -*-
"""
Caso de Uso de Aplicación: Liberar Registros Huérfanos
Watchdog Sweeper que libera registros que quedaron en estado 'procesando'
tras desconexiones o fallos imprevistos de workers.
"""
from core.ports.queue_port import IColaRepositorioPort

class LiberarHuerfanosUseCase:
    def __init__(self, cola_repo: IColaRepositorioPort):
        self.cola = cola_repo

    def ejecutar(self, minutos_inactividad: int = 15) -> int:
        """Libera los registros y retorna la cantidad recuperada."""
        return self.cola.liberar_huerfanos(minutos_inactividad=minutos_inactividad)
