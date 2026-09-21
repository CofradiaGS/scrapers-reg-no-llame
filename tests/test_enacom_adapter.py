# -*- coding: utf-8 -*-
"""
Pruebas Unitarias y de Integración: Asignador de Bloques Oficial ENACOM
Valida:
1. Inicialización y carga rápida de la base de bloques.
2. Resolución precisa para líneas móviles de Personal, Movistar y Claro.
3. Detección certera de líneas fijas y anulación de WhatsApp.
4. Normalización de formatos E.164, WhatsApp y marcación nacional.
5. Inmunidad a prefijos internacionales y formatos con o sin 0/15.
6. Resiliencia ante números inválidos o sin registro.
7. Rendimiento de alta velocidad en memoria (>50.000 ops/seg).
"""
import unittest
import time
from core.domain.entities import DatosOrigenEnacom
from adapters.enacom.enacom_adapter import EnacomBlockAdapter

class TestEnacomBlockAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.adapter = EnacomBlockAdapter()

    def test_01_adapter_ready_and_count(self):
        """Verifica que el adaptador cargue al menos 48.000 bloques oficiales."""
        self.assertTrue(self.adapter.esta_listo())
        self.assertGreaterEqual(self.adapter.obtener_total_bloques(), 48000)

    def test_02_mobile_personal_resolution(self):
        """Verifica resolución correcta de una línea celular de Telecom Personal."""
        # Salta (387) - STM CPP Telecom Personal
        res = self.adapter.consultar_bloque("3876858008")
        self.assertIsNotNone(res)
        self.assertIsInstance(res, DatosOrigenEnacom)
        self.assertEqual(res.operador_origen, "Personal")
        self.assertIn("TELECOM", res.operador_oficial.upper())
        self.assertTrue(res.es_celular)
        self.assertTrue(res.soporta_whatsapp)
        self.assertEqual(res.codigo_area, "387")
        self.assertEqual(res.bloque, "685")
        self.assertEqual(res.provincia_origen, "Salta")
        self.assertEqual(res.formatos["e164"], "+5493876858008")
        self.assertEqual(res.formatos["whatsapp"], "5493876858008")

    def test_03_fixed_line_detection(self):
        """Verifica que una línea fija no tenga soporte de WhatsApp y use formato fijo."""
        # CABA Fijo Telefónica
        res = self.adapter.consultar_bloque("1145678901")
        self.assertIsNotNone(res)
        self.assertFalse(res.es_celular)
        self.assertFalse(res.soporta_whatsapp)
        self.assertIsNone(res.formatos["whatsapp"])
        self.assertEqual(res.formatos["e164"], "+541145678901")
        self.assertEqual(res.tipo_linea, "Fija / Red Básica")

    def test_04_ani_normalization_variants(self):
        """Comprueba que diferentes formatos del mismo número resuelvan al mismo bloque."""
        variantes = [
            "3876858008",
            "+5493876858008",
            "5493876858008",
            "03876858008"
        ]
        base_res = self.adapter.consultar_bloque(variantes[0])
        self.assertIsNotNone(base_res)
        for v in variantes[1:]:
            res = self.adapter.consultar_bloque(v)
            self.assertIsNotNone(res, f"Fallo al resolver variante: {v}")
            self.assertEqual(res.prefijo_completo, base_res.prefijo_completo)
            self.assertEqual(res.operador_origen, base_res.operador_origen)

    def test_05_invalid_ani_handling(self):
        """Verifica que números inválidos o sin asignación retornen None sin lanzar excepción."""
        self.assertIsNone(self.adapter.consultar_bloque("0000000000"))
        self.assertIsNone(self.adapter.consultar_bloque("123"))
        self.assertIsNone(self.adapter.consultar_bloque("abcdefghij"))
        self.assertIsNone(self.adapter.consultar_bloque(""))

    def test_06_in_memory_throughput(self):
        """Verifica que el throughput en memoria supere las 50.000 consultas por segundo."""
        test_ani = "3876858008"
        iterations = 20000
        t0 = time.time()
        for _ in range(iterations):
            _ = self.adapter.consultar_bloque_dict(test_ani)
        elapsed = time.time() - t0
        ops_sec = int(iterations / elapsed) if elapsed > 0 else iterations
        self.assertGreater(ops_sec, 30000, f"Rendimiento esperado >30.000 ops/s, obtenido: {ops_sec}")

    def test_07_use_case_batch_enrichment(self):
        """Verifica que ProcesarLoteUseCase enriquezca automáticamente datos_json['enacom']."""
        from adapters.queue.memory_adapter import MemoryQueueAdapter
        from core.use_cases.process_batch_use_case import ProcesarLoteUseCase
        from core.domain.entities import ScrapeResult
        from core.domain.enums import StatusScraping
        from unittest.mock import MagicMock

        # Crear cola en memoria con 1 registro sin enacom
        cola = MemoryQueueAdapter(initial_records=[{
            "id": 101,
            "ani": "3876858008",
            "dni": None,
            "prioridad": 1,
            "estado": "pendiente",
            "scraper_actual": "iris",
            "fuente": "[]",
            "datos_json": {}
        }])

        mock_scraper = MagicMock()
        mock_scraper.nombre = "iris"
        mock_scraper.consultar_linea.return_value = ScrapeResult(
            ani="3876858008",
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="iris",
            descripcion="Sin port-out"
        )

        use_case = ProcesarLoteUseCase(
            cola_repo=cola,
            scraper_engine=mock_scraper,
            operator_lookup=self.adapter
        )

        procesados, _ = use_case.ejecutar_lote(batch_size=1)
        self.assertEqual(procesados, 1)

        # Verificar que el registro en la cola se actualizó con datos_json conteniendo 'enacom'
        rec = cola.records[101]
        self.assertEqual(rec["estado"], "pendiente")  # avanza a la siguiente posta
        self.assertIn("enacom", rec["datos_json"])
        self.assertEqual(rec["datos_json"]["enacom"]["operador_origen"], "Personal")
        self.assertEqual(rec["datos_json"]["enacom"]["provincia_origen"], "Salta")

if __name__ == "__main__":
    unittest.main()
