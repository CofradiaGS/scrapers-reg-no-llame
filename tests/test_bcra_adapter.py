# -*- coding: utf-8 -*-
"""
Pruebas Unitarias del Adaptador BCRA (BcraAdapter).
Verifica:
1. Registro en ScraperRegistry.
2. Fast-path ante falta de DNI / CUIT.
3. Parseo financiero: deudas en miles y pesos reales, operaciones en cartera y cantidad de entidades.
4. Caso 404 / sin deuda registrada.
"""
import unittest
from unittest.mock import MagicMock, patch
from core.domain.entities import Linea
from core.domain.enums import StatusScraping
from adapters.scrapers.registry import ScraperRegistry
from adapters.scrapers.bcra.bcra_adapter import BcraAdapter, BCRAGlobalRateLimiter


class TestBcraAdapter(unittest.TestCase):

    def setUp(self):
        # Usamos rate limiter con intervalo 0 para pruebas rápidas
        self.fast_limiter = BCRAGlobalRateLimiter(min_interval=0.0)
        self.adapter = BcraAdapter(rate_limiter=self.fast_limiter)

    def test_01_registro_en_scraper_registry(self):
        scraper_cls = ScraperRegistry._registry.get("bcra")
        self.assertIsNotNone(scraper_cls)
        self.assertEqual(scraper_cls, BcraAdapter)

    def test_02_sin_dni_fast_path(self):
        linea = Linea(ani="1144556677", dni=None)
        res = self.adapter.consultar_linea(linea)
        self.assertEqual(res.status, StatusScraping.SIN_COINCIDENCIA)
        self.assertIn("Sin DNI ni CUIT", res.descripcion)

    @patch("requests.Session.get")
    def test_03_consulta_con_deuda_parseo_financiero(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "results": {
                "denominacion": "PEREZ JUAN",
                "periodos": [
                    {
                        "periodo": "202408",
                        "entidades": [
                            {
                                "entidad": "BANCO MACRO S.A.",
                                "situacion": 2,
                                "monto": 150.5,
                                "diasAtrasoPago": 35,
                                "procesoJud": False
                            },
                            {
                                "entidad": "BANCO DE GALICIA Y BUENOS AIRES S.A.U.",
                                "situacion": 1,
                                "monto": 50.0,
                                "diasAtrasoPago": 0,
                                "procesoJud": False
                            }
                        ]
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        self.adapter.iniciar()
        linea = Linea(ani="1144556677", dni="20301122331")
        res = self.adapter.consultar_linea(linea)

        self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
        det = res.detalles
        self.assertEqual(det["denominacion"], "PEREZ JUAN")
        self.assertEqual(det["peor_situacion"], 2)
        self.assertEqual(det["cantidad_entidades"], 2)
        self.assertEqual(det["operaciones_en_cartera"], 2)
        self.assertEqual(det["deuda_total_miles"], 200.5)
        self.assertEqual(det["deuda_total_pesos"], 200500.0)
        self.assertEqual(det["deuda_macro_miles"], 150.5)
        self.assertEqual(det["deuda_macro_pesos"], 150500.0)
        self.assertEqual(det["deuda_macro_situacion"], 2)
        self.assertFalse(det["sin_deuda"])

    @patch("requests.Session.get")
    def test_04_consulta_404_sin_deuda(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        self.adapter.iniciar()
        linea = Linea(ani="1144556677", dni="20301122331")
        res = self.adapter.consultar_linea(linea)

        self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
        det = res.detalles
        self.assertTrue(det["sin_deuda"])
        self.assertEqual(det["peor_situacion"], 0)
        self.assertEqual(det["deuda_total_pesos"], 0.0)
        self.assertEqual(det["cantidad_entidades"], 0)
        self.assertEqual(det["operaciones_en_cartera"], 0)


if __name__ == "__main__":
    unittest.main()
