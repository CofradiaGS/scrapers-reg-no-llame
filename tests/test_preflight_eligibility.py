# -*- coding: utf-8 -*-
"""
Pruebas de Unidad: Pre-flight de Elegibilidad en ProcesarLoteUseCase
Verifica que las líneas no aptas (ej. Claro sin DNI, o con coincidencia previa)
sean salteadas sin invocar a consultar_linea en el scraper y transicionadas en milisegundos.
"""
import unittest
from core.use_cases.process_batch_use_case import ProcesarLoteUseCase
from adapters.queue.memory_adapter import MemoryQueueAdapter
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import ScrapeResult, Linea
from core.domain.enums import StatusScraping

class DummyScraper(IScraperEnginePort):
    def __init__(self, nombre="claro"):
        self._nombre = nombre
        self.consultar_llamado = 0

    @property
    def nombre(self) -> str:
        return self._nombre

    def iniciar(self): pass
    def autenticar(self) -> bool: return True
    def cerrar(self): pass
    def verificar_salud(self) -> bool: return True

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        self.consultar_llamado += 1
        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper=self.nombre,
            descripcion="Consulta dummy"
        )

class TestPreflightEligibility(unittest.TestCase):

    def test_claro_sin_dni_saltea_http_y_avanza_a_personal(self):
        """
        Un lote asignado a Claro donde las líneas no tienen DNI debe saltear
        la invocación a consultar_linea (0 llamadas de red) y transicionar
        las líneas inmediatamente a 'personal' en estado 'pendiente'.
        """
        cola = MemoryQueueAdapter([
            {
                "id": 101,
                "ani": "1123456789",
                "scraper_actual": "claro",
                "estado": "pendiente",
                "datos_json": {"iris": {"status": "sin_coincidencia"}}  # Sin DNI
            },
            {
                "id": 102,
                "ani": "1198765432",
                "scraper_actual": "claro",
                "estado": "pendiente",
                "datos_json": {}  # Sin DNI
            }
        ])

        scraper = DummyScraper(nombre="claro")
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=scraper)

        items_procesados = []
        def on_item(item):
            items_procesados.append(item)

        proc_count, sin_proc = use_case.ejecutar_lote(
            batch_size=10,
            on_item_procesado=on_item
        )

        # 1. Verificar que se procesaron 2 registros
        self.assertEqual(proc_count, 2)
        self.assertEqual(len(sin_proc), 0)

        # 2. Cero llamadas HTTP al scraper
        self.assertEqual(scraper.consultar_llamado, 0, "No debió llamarse a consultar_linea para líneas sin DNI")

        # 3. Ambos ítems avanzaron a 'personal'
        self.assertEqual(items_procesados[0]["scraper_actual"], "personal")
        self.assertEqual(items_procesados[0]["estado"], "pendiente")
        self.assertEqual(items_procesados[1]["scraper_actual"], "personal")
        self.assertEqual(items_procesados[1]["estado"], "pendiente")

        # 4. Estado en la cola
        reg_101 = cola.records[101]
        self.assertEqual(reg_101["scraper_actual"], "personal")
        self.assertEqual(reg_101["estado"], "pendiente")

    def test_claro_con_dni_ejecuta_scraper_normalmente(self):
        """
        Una línea para Claro con DNI disponible sí debe ejecutar consultar_linea.
        """
        cola = MemoryQueueAdapter([
            {
                "id": 201,
                "ani": "1123456789",
                "scraper_actual": "claro",
                "estado": "pendiente",
                "datos_json": {
                    "iris": {
                        "titular": {"nro_documento": "33517690"}
                    }
                }
            }
        ])

        scraper = DummyScraper(nombre="claro")
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=scraper)

        items_procesados = []
        proc_count, sin_proc = use_case.ejecutar_lote(
            batch_size=10,
            on_item_procesado=lambda it: items_procesados.append(it)
        )

        self.assertEqual(proc_count, 1)
        self.assertEqual(scraper.consultar_llamado, 1, "Debió ejecutarse la consulta porque la línea tiene DNI")
        self.assertEqual(items_procesados[0]["status"], StatusScraping.SIN_COINCIDENCIA.value)

    def test_fuera_de_horario_comercial_aborta_y_revierte_a_pendiente(self):
        """
        Si durante el lote el scraper lanza FueraDeHorarioComercialException,
        el caso de uso debe abortar inmediatamente el lote sin marcar error,
        devolviendo el registro actual y los restantes a 'pendiente'.
        """
        from core.domain.exceptions import FueraDeHorarioComercialException

        class HorarioScraper(DummyScraper):
            def consultar_linea(self, linea: Linea) -> ScrapeResult:
                raise FueraDeHorarioComercialException("Fuera de horario comercial")

        cola = MemoryQueueAdapter([
            {"id": 301, "ani": "1123456789", "scraper_actual": "iris", "estado": "pendiente"},
            {"id": 302, "ani": "1198765432", "scraper_actual": "iris", "estado": "pendiente"}
        ])
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=HorarioScraper(nombre="iris"))
        proc, sin_proc = use_case.ejecutar_lote(batch_size=10)
        self.assertEqual(proc, 0)
        self.assertEqual(len(sin_proc), 2)
        self.assertEqual(cola.records[301]["estado"], "pendiente")
        self.assertEqual(cola.records[302]["estado"], "pendiente")

    def test_should_pause_aborta_lote_y_revierte_a_pendiente(self):
        """
        Si should_pause() es True en mitad del lote, debe abortar y revertir a pendiente.
        """
        cola = MemoryQueueAdapter([
            {"id": 401, "ani": "1123456789", "scraper_actual": "iris", "estado": "pendiente"},
            {"id": 402, "ani": "1198765432", "scraper_actual": "iris", "estado": "pendiente"}
        ])
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=DummyScraper(nombre="iris"))
        proc, sin_proc = use_case.ejecutar_lote(batch_size=10, should_pause=lambda: True)
        self.assertEqual(proc, 0)
        self.assertEqual(len(sin_proc), 2)
        self.assertEqual(cola.records[401]["estado"], "pendiente")
        self.assertEqual(cola.records[402]["estado"], "pendiente")

if __name__ == "__main__":
    unittest.main()
