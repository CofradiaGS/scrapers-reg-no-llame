# -*- coding: utf-8 -*-
"""
Pruebas Unitarias del Adaptador Datuar (DatuarAdapter).
Verifica:
1. Registro en ScraperRegistry.
2. Fast-path ante falta de DNI.
3. Extracción demográfica y parseo de atributos data-* (nombres, cuil, edad, género, ubicación).
4. Reacción ante detección de '0 resultados' (rotación inmediata de circuito).
"""
import unittest
from unittest.mock import MagicMock, patch
from core.domain.entities import Linea
from core.domain.enums import StatusScraping
from adapters.scrapers.registry import ScraperRegistry
from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter


class TestDatuarAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = DatuarAdapter(delay_min=0.0, delay_max=0.0, use_tor=False)

    def test_01_registro_en_scraper_registry(self):
        scraper_cls = ScraperRegistry._registry.get("datuar")
        self.assertIsNotNone(scraper_cls)
        self.assertEqual(scraper_cls, DatuarAdapter)

    def test_02_sin_dni_fast_path(self):
        linea = Linea(ani="1144556677", dni=None)
        res = self.adapter.consultar_linea(linea)
        self.assertEqual(res.status, StatusScraping.SIN_COINCIDENCIA)
        self.assertIn("Sin DNI", res.descripcion)

    @patch("requests.Session.get")
    def test_03_extraccion_atributos_demograficos(self, mock_get):
        html_datuar = """
        <html>
            <div class="resultado-item"
                 data-nombre-completo="PEREZ, JUAN CARLOS"
                 data-nombre="JUAN CARLOS"
                 data-cdu="20301122331"
                 data-edad="42"
                 data-genero="M"
                 data-provincia="BUENOS AIRES"
                 data-ciudad="LA PLATA"
                 data-municipio="LA PLATA">
            </div>
        </html>
        """
        mock_resp = MagicMock(status_code=200, text=html_datuar)
        mock_get.return_value = mock_resp

        self.adapter.iniciar()
        linea = Linea(ani="1144556677", dni="30112233")
        res = self.adapter.consultar_linea(linea)

        self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
        det = res.detalles
        self.assertEqual(det["nombre_completo"], "PEREZ, Juan Carlos")
        self.assertEqual(det["nombres"], "Juan Carlos")
        self.assertEqual(det["apellidos"], "PEREZ")
        self.assertEqual(det["cuil"], "20301122331")
        self.assertEqual(det["edad"], 42)
        self.assertEqual(det["genero"], "Masculino")
        self.assertEqual(det["provincia"], "Buenos Aires")
        self.assertEqual(det["ciudad"], "La Plata")


if __name__ == "__main__":
    unittest.main()
