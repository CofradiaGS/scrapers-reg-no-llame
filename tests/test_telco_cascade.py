# -*- coding: utf-8 -*-
"""
Pruebas Unitarias del Motor de Cascada Telco (TelcoCascadeAdapter)
Arquitectura Hexagonal - Cortocircuito y Acumulación de Metadatos

Verifica los 4 caminos del pipeline:
1. Coincidencia Claro (con DNI) -> Cortocircuito inmediato a finalizado.
2. Coincidencia Personal (ANI) -> Cortocircuito inmediato a finalizado.
3. Coincidencia Movistar (ANI) -> Cortocircuito inmediato a finalizado.
4. Sin coincidencias en ninguna compañía -> Retorna SIN_COINCIDENCIA tras consultar todas.
"""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.domain.entities import Linea, ScrapeResult, Titular
from core.domain.enums import StatusScraping
from adapters.scrapers.telcos.telco_cascade_adapter import TelcoCascadeAdapter


class TestTelcoCascadeAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = TelcoCascadeAdapter()

    def test_01_cortocircuito_claro(self):
        linea = Linea(ani="1144001122", dni="30112233")
        res_claro_mock = ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="claro",
            operador="Claro",
            titular=Titular(nro_documento="30112233", nombre="JUAN", apellido="PEREZ"),
            detalles={"deuda": 2500.50, "comprobantes": [{"numero": "F-001", "importe": 2500.50}]},
            descripcion="Coincidencia Claro Deuda: $2500.50"
        )

        with patch.object(self.adapter.claro, "consultar_linea", return_value=res_claro_mock) as mock_claro:
            with patch.object(self.adapter.personal, "consultar_linea") as mock_personal:
                with patch.object(self.adapter.movistar, "consultar_linea") as mock_movistar:
                    resultado = self.adapter.consultar_linea(linea)

                    self.assertEqual(resultado.status, StatusScraping.COINCIDENCIA)
                    self.assertEqual(resultado.operador, "Claro")
                    self.assertEqual(resultado.fuente_scraper, "claro")
                    mock_claro.assert_called_once()
                    # Verificar que Personal y Movistar NO fueron llamados (cortocircuito estricto)
                    mock_personal.assert_not_called()
                    mock_movistar.assert_not_called()

    def test_02_cortocircuito_personal(self):
        linea = Linea(ani="2614556677", dni=None)
        res_claro_no = ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="claro",
            operador="Claro",
            descripcion="Sin deuda en Claro"
        )
        res_pers_mock = ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="personal",
            operador="Telecom Personal",
            detalles={"deuda": 1800.0, "modalidad": "postpago"},
            descripcion="Coincidencia Telecom Personal"
        )

        with patch.object(self.adapter.claro, "consultar_linea", return_value=res_claro_no):
            with patch.object(self.adapter.personal, "consultar_linea", return_value=res_pers_mock) as mock_personal:
                with patch.object(self.adapter.movistar, "consultar_linea") as mock_movistar:
                    resultado = self.adapter.consultar_linea(linea)

                    self.assertEqual(resultado.status, StatusScraping.COINCIDENCIA)
                    self.assertEqual(resultado.operador, "Telecom Personal")
                    self.assertEqual(resultado.fuente_scraper, "personal")
                    mock_personal.assert_called_once()
                    # Movistar no debe ser consultado
                    mock_movistar.assert_not_called()

    def test_03_cortocircuito_movistar(self):
        linea = Linea(ani="3416554433", dni=None)
        res_claro_no = ScrapeResult(ani=linea.ani, status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="claro", operador="Claro")
        res_pers_no = ScrapeResult(ani=linea.ani, status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="personal", operador="Personal")
        res_mov_mock = ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="movistar",
            operador="Movistar",
            detalles={"deuda": 3200.0},
            descripcion="Coincidencia Movistar"
        )

        with patch.object(self.adapter.claro, "consultar_linea", return_value=res_claro_no):
            with patch.object(self.adapter.personal, "consultar_linea", return_value=res_pers_no):
                with patch.object(self.adapter.movistar, "consultar_linea", return_value=res_mov_mock) as mock_movistar:
                    resultado = self.adapter.consultar_linea(linea)

                    self.assertEqual(resultado.status, StatusScraping.COINCIDENCIA)
                    self.assertEqual(resultado.operador, "Movistar")
                    self.assertEqual(resultado.fuente_scraper, "movistar")
                    mock_movistar.assert_called_once()

    def test_04_sin_coincidencias_acumulado(self):
        linea = Linea(ani="1199887766", dni=None)
        res_claro_no = ScrapeResult(ani=linea.ani, status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="claro", operador="Claro")
        res_pers_no = ScrapeResult(ani=linea.ani, status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="personal", operador="Personal")
        res_mov_no = ScrapeResult(ani=linea.ani, status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="movistar", operador="Movistar")

        with patch.object(self.adapter.claro, "consultar_linea", return_value=res_claro_no):
            with patch.object(self.adapter.personal, "consultar_linea", return_value=res_pers_no):
                with patch.object(self.adapter.movistar, "consultar_linea", return_value=res_mov_no):
                    resultado = self.adapter.consultar_linea(linea)

                    self.assertEqual(resultado.status, StatusScraping.SIN_COINCIDENCIA)
                    self.assertEqual(resultado.fuente_scraper, "telcos")
                    # Debe tener los datos acumulados solo de las 3 compañías (sin ENACOM)
                    acum = resultado.detalles.get("datos_acumulados", {})
                    self.assertIn("claro", acum)
                    self.assertIn("personal", acum)
                    self.assertIn("movistar", acum)
                    self.assertNotIn("enacom", acum)



if __name__ == "__main__":
    unittest.main()
