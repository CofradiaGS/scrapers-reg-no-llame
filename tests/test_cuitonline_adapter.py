# -*- coding: utf-8 -*-
"""
Pruebas Unitarias del Adaptador CuitOnline (CuitOnlineAdapter).
Verifica:
1. Registro en ScraperRegistry.
2. Fast-path ante falta de DNI.
3. Blindaje Anti-Honeypot: Detección y descarte de trampas MD5 y CUITs falsos.
4. Normalización de Condición AFIP e inferencia de género.
"""
import unittest
from unittest.mock import MagicMock, patch
from core.domain.entities import Linea
from core.domain.enums import StatusScraping
from adapters.scrapers.registry import ScraperRegistry
from adapters.scrapers.cuitonline.cuitonline_adapter import CuitOnlineAdapter


class TestCuitOnlineAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = CuitOnlineAdapter(delay_min=0.0, delay_max=0.0, use_tor=False)

    def test_01_registro_en_scraper_registry(self):
        scraper_cls = ScraperRegistry._registry.get("cuitonline")
        self.assertIsNotNone(scraper_cls)
        self.assertEqual(scraper_cls, CuitOnlineAdapter)

    def test_02_sin_dni_fast_path(self):
        linea = Linea(ani="1144556677", dni=None)
        res = self.adapter.consultar_linea(linea)
        self.assertEqual(res.status, StatusScraping.SIN_COINCIDENCIA)
        self.assertIn("Sin DNI", res.descripcion)

    @patch("requests.Session.get")
    def test_03_anti_honeypot_descarta_trampa_md5(self, mock_get):
        # Simula respuesta con hit tramposo (enlace con hash MD5 en vez de CUIT numérico)
        html_honeypot = """
        <html>
            <div class="hit">
                <div class="denominacion"><a href="/detalle/bcb827a0bfd991c499264fa58897d2e3/trampa"><h2>PERSONA TRAMPA</h2></a></div>
                <div class="cuit">20-12345678-9</div>
            </div>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_honeypot
        mock_get.return_value = mock_resp

        self.adapter.iniciar()
        linea = Linea(ani="1144556677", dni="30112233")
        res = self.adapter.consultar_linea(linea)

        # Debe descartar la trampa y retornar SIN_COINCIDENCIA
        self.assertEqual(res.status, StatusScraping.SIN_COINCIDENCIA)

    @patch("requests.Session.get")
    def test_04_hit_valido_con_condicion_afip(self, mock_get):
        # Hit válido con CUIT numérico 20301122331
        html_search = """
        <html>
            <div class="hit">
                <div class="denominacion"><a href="/detalle/20301122331/perez-juan"><h2>PEREZ JUAN</h2></a></div>
                <div class="cuit">20-30112233-1</div>
                <div class="doc-facets">Persona Física - Masculino<br>Ganancias: No Inscripto<br>IVA: Exento</div>
            </div>
        </html>
        """
        # Detalle
        html_detail = """
        <html>
            <div class="persona-data">
                <div class="p_cuit">20-30112233-1</div>
                <span itemprop="streetAddress">AV CORRIENTES 1234</span>
                <span itemprop="addressLocality">CAPITAL FEDERAL</span>
                <span itemprop="addressRegion">BUENOS AIRES</span>
                <li>
                    <h2 class="impuestos_activos">Impuestos activos</h2>
                    <ul>
                        <li>MONOTRIBUTO</li>
                    </ul>
                </li>
            </div>
        </html>
        """
        resp_search = MagicMock(status_code=200, text=html_search)
        resp_detail = MagicMock(status_code=200, text=html_detail)
        mock_get.side_effect = [resp_search, resp_detail]

        self.adapter.iniciar()
        linea = Linea(ani="1144556677", dni="30112233")
        res = self.adapter.consultar_linea(linea)

        self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
        det = res.detalles
        self.assertEqual(det["cuit_limpio"], "20301122331")
        self.assertEqual(det["genero"], "Masculino")
        self.assertEqual(det["condicion_afip"], "Monotributista")
        self.assertEqual(det["direccion"], "AV CORRIENTES 1234")


if __name__ == "__main__":
    unittest.main()
