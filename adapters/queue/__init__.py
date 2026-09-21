# -*- coding: utf-8 -*-
"""
Alias de compatibilidad: adapters.queue redirige a adapters.db
"""
from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter
from adapters.queue.memory_adapter import MemoryQueueAdapter
from adapters.queue.ipc_adapter import IPCWorkerQueueAdapter

__all__ = ["MySQLQueueAdapter", "MemoryQueueAdapter", "IPCWorkerQueueAdapter"]
