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
from core.domain.entities import ScrapeResult, Linea, ReglaPipeline
from core.domain.enums import StatusScraping, EstadoRegistro

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

    def test_ipc_streaming_persistence(self):
        """
        Si la cola tiene is_ipc=True, el caso de uso debe persistir cada ítem
        inmediatamente en streaming a la cola en RAM para no retener datos en el worker.
        """
        class MockIPCQueue(MemoryQueueAdapter):
            is_ipc = True
            def __init__(self, records):
                super().__init__(records)
                self.calls_persist = []

            def persistir_resultados(self, resultados):
                self.calls_persist.append(resultados)
                return super().persistir_resultados(resultados)

        cola = MockIPCQueue([
            {"id": 501, "ani": "1123456789", "scraper_actual": "iris", "estado": "pendiente"},
            {"id": 502, "ani": "1198765432", "scraper_actual": "iris", "estado": "pendiente"}
        ])
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=DummyScraper(nombre="iris"))
        proc, sin_proc = use_case.ejecutar_lote(batch_size=10)

        self.assertEqual(proc, 2)
        # Debe haberse llamado persistir_resultados 2 veces (1 por ítem), NO 1 vez al final
        self.assertEqual(len(cola.calls_persist), 2)
        self.assertEqual(len(cola.calls_persist[0]), 1)
        self.assertEqual(len(cola.calls_persist[1]), 1)

    def test_active_en_reg_no_llame_rechazado_en_dominio_para_todos_los_scrapers(self):
        """
        ReglaPipeline.es_elegible_para_scraper debe rechazar tajantemente cualquier
        línea con active=True para TODOS los scrapers sin excepción.
        """
        scrapers = ["iris", "telcos", "claro", "personal", "movistar", "datuar", "cuitonline", "bcra"]
        for sc in scrapers:
            apto, motivo = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre=sc,
                ani="1123456789",
                dni="30112233",
                datos_json={},
                active=True
            )
            self.assertFalse(apto, f"El scraper {sc} no debió admitir una línea con active=True")
            self.assertIn("active=True", motivo)

    def test_active_false_o_none_permite_evaluacion_normal(self):
        """
        Si active es False o None, la línea debe evaluarse según sus reglas habituales.
        """
        for act in (False, None):
            apto, motivo = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre="iris",
                ani="1123456789",
                active=act
            )
            self.assertTrue(apto, f"IRIS debió aceptar la línea con active={act}")

    def test_active_true_en_use_case_no_scrapea_y_rerevierte_a_pendiente(self):
        """
        Si un registro con active=True llega al caso de uso, se debe abortar la consulta HTTP,
        no mutar sus datos y revertirlo a 'pendiente' para no quemarlo ni finalizarlo.
        """
        class IngestaQueue(MemoryQueueAdapter):
            pass

        # Forzar un registro con active=True
        cola = IngestaQueue([
            {
                "id": 601,
                "ani": "1144556677",
                "scraper_actual": "iris",
                "estado": "pendiente",
                "active": False  # Para que reservar_lote lo tome en la prueba
            }
        ])

        # Simulamos que al reclamarlo se detectó active=True
        lote_reservado = cola.reservar_lote(batch_size=1)
        self.assertEqual(len(lote_reservado), 1)
        lote_reservado[0].active = True
        lote_reservado[0].tipo_cola = "registro_no_llame"

        scraper = DummyScraper(nombre="iris")
        use_case = ProcesarLoteUseCase(cola_repo=cola, scraper_engine=scraper)

        # Mockeamos reservar_lote para que retorne directamente este lote con active=True
        cola.reservar_lote = lambda *args, **kwargs: lote_reservado

        items_procesados = []
        proc, sin_proc = use_case.ejecutar_lote(
            batch_size=1,
            on_item_procesado=lambda it: items_procesados.append(it)
        )

        self.assertEqual(scraper.consultar_llamado, 0, "No debe invocar consultar_linea para registros activos en No Llame")
        self.assertEqual(proc, 1)
        self.assertEqual(items_procesados[0]["estado"], EstadoRegistro.PENDIENTE.value)
        self.assertEqual(items_procesados[0]["scraper_actual"], "iris")
        self.assertIn("Línea activa en Registro No Llame", items_procesados[0]["descripcion"])

    def test_memory_queue_adapter_filtra_active_true(self):
        """
        MemoryQueueAdapter.reservar_lote no debe reclamar registros que tengan active=True.
        """
        cola = MemoryQueueAdapter([
            {"id": 701, "ani": "1111111111", "scraper_actual": "iris", "estado": "pendiente", "active": True},
            {"id": 702, "ani": "1122222222", "scraper_actual": "iris", "estado": "pendiente", "active": False},
            {"id": 703, "ani": "1133333333", "scraper_actual": "iris", "estado": "pendiente"}  # None
        ])

        lote = cola.reservar_lote(batch_size=10)
        ids_reservados = [r.id for r in lote]

        self.assertNotIn(701, ids_reservados, "El registro con active=True no debe ser reservado")
        self.assertIn(702, ids_reservados, "El registro con active=False sí debe ser reservado")
        self.assertIn(703, ids_reservados, "El registro con active=None sí debe ser reservado")

if __name__ == "__main__":
    unittest.main()

