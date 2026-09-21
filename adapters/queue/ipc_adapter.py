# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Cola IPC Multiproc (Inter-Process Communication)
Implementa IColaRepositorioPort mediante colas de memoria compartida (multiprocessing.Queue).

Permite que un proceso worker en subproceso solicite lotes y persista resultados
delegando las operaciones de base de datos directamente al hilo despachador del
Supervisor maestro en la misma máquina física.

Ventajas Arquitectónicas:
1. Elimina la sobrecarga de conexiones TCP/TLS: 0 conexiones remotas por worker.
2. Latencia en memoria RAM inferior a 0.1 ms para la comunicación inter-proceso.
3. El Supervisor unifica el 100% de las consultas de la máquina en 1 sola conexión persistente.
4. Cumplimiento estricto del contrato IColaRepositorioPort (Arquitectura Hexagonal).
"""
import time
import logging
from typing import List, Dict, Any, Optional
from multiprocessing import Queue
from queue import Empty

from core.ports.queue_port import IColaRepositorioPort
from core.domain.entities import RegistroCola

logger = logging.getLogger("IPCWorkerQueueAdapter")


class IPCWorkerQueueAdapter(IColaRepositorioPort):
    """
    Adaptador de cola para subprocesos Worker.
    Delega las operaciones de persistencia y reclamo al Supervisor mediante canales IPC.
    """

    def __init__(
        self,
        slot_id: int,
        request_queue: Queue,
        response_queue: Queue,
        timeout: float = 60.0
    ):
        self.slot_id = slot_id
        self.request_queue = request_queue
        self.response_queue = response_queue
        self.timeout = timeout

    def reservar_lote(
        self,
        batch_size: int = 15,
        prioridad: Optional[int] = None,
        scraper_nombre: str = "iris",
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        """Solicita al despachador del Supervisor la reserva de un micro-lote."""
        payload = {
            "action": "reservar_lote",
            "slot_id": self.slot_id,
            "batch_size": batch_size,
            "prioridad": prioridad,
            "scraper_nombre": scraper_nombre,
            "solo_sin_coincidencia": solo_sin_coincidencia
        }
        try:
            self.request_queue.put(payload)
            resp = self.response_queue.get(timeout=self.timeout)
            if isinstance(resp, dict) and resp.get("error"):
                logger.error(f"[Slot {self.slot_id}] Error en respuesta de reserva IPC: {resp['error']}")
                return []
            if isinstance(resp, list):
                return resp
            return []
        except Empty:
            logger.error(f"[Slot {self.slot_id}] Timeout ({self.timeout}s) esperando reserva de lote desde el Supervisor.")
            return []
        except Exception as e:
            logger.error(f"[Slot {self.slot_id}] Fallo en comunicación IPC de reserva: {e}")
            return []

    def persistir_resultados(self, resultados: List[Dict[str, Any]]) -> bool:
        """Envía al despachador del Supervisor los resultados a persistir en MySQL."""
        if not resultados:
            return True

        payload = {
            "action": "persistir_resultados",
            "slot_id": self.slot_id,
            "resultados": resultados
        }
        try:
            self.request_queue.put(payload)
            resp = self.response_queue.get(timeout=self.timeout)
            if isinstance(resp, dict) and resp.get("error"):
                logger.error(f"[Slot {self.slot_id}] Error en persistencia IPC: {resp['error']}")
                return False
            return bool(resp)
        except Empty:
            logger.error(f"[Slot {self.slot_id}] Timeout ({self.timeout}s) esperando confirmación de persistencia IPC.")
            return False
        except Exception as e:
            logger.error(f"[Slot {self.slot_id}] Fallo en comunicación IPC de persistencia: {e}")
            return False

    def revertir_a_pendiente(self, ids: List[int]) -> bool:
        """Solicita revertir registros procesando a pendiente ante apagado o parada."""
        if not ids:
            return True

        payload = {
            "action": "revertir_a_pendiente",
            "slot_id": self.slot_id,
            "ids": ids
        }
        try:
            self.request_queue.put(payload)
            resp = self.response_queue.get(timeout=self.timeout)
            return bool(resp)
        except Exception as e:
            logger.error(f"[Slot {self.slot_id}] Error al revertir a pendiente vía IPC: {e}")
            return False

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        """
        No-op en el worker: El barrido de huérfanos se ejecuta exclusivamente
        en el hilo centinela del Supervisor maestro.
        """
        return 0

    def obtener_estadisticas(self) -> Dict[str, int]:
        """Solicita estadísticas al despachador del Supervisor."""
        payload = {
            "action": "obtener_estadisticas",
            "slot_id": self.slot_id
        }
        try:
            self.request_queue.put(payload)
            resp = self.response_queue.get(timeout=self.timeout)
            return resp if isinstance(resp, dict) else {}
        except Exception as e:
            logger.error(f"[Slot {self.slot_id}] Error consultando estadísticas vía IPC: {e}")
            return {}
