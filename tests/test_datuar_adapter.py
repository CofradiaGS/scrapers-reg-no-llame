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
from adapters.network.tor_controller import TorController


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

    @patch.object(TorController, "is_running", return_value=True)
    @patch("requests.Session.get")
    def test_04_bloqueo_dispara_transient_error_y_rotacion(self, mock_get, mock_is_running):
        from core.domain.exceptions import ScraperTransientError

        html_bloqueo = """
        <html>
            <p class="text-danger mb-0 fs-5 fw-bold"><i class="fa-solid fa-shield-halved me-2"></i> Has superado el límite de búsquedas gratuitas continuas. Es obligatorio completar la verificación de seguridad para continuar.</p>
        </html>
        """
        mock_resp = MagicMock(status_code=200, text=html_bloqueo)
        mock_get.return_value = mock_resp

        adapter = DatuarAdapter(delay_min=0.0, delay_max=0.0, use_tor=True, tor_external_daemon=True, worker_slot=3)
        adapter.iniciar()

        with patch.object(adapter, "_rotar_circuito_instantaneo", wraps=adapter._rotar_circuito_instantaneo) as mock_rotar:
            linea = Linea(ani="1144556677", dni="30112233")
            with self.assertRaises(ScraperTransientError) as ctx:
                adapter.consultar_linea(linea)

            self.assertIn("límite de búsquedas o verificación", str(ctx.exception))
            # Verifica que se llamó a _rotar_circuito_instantaneo con es_bloqueo=True
            self.assertTrue(mock_rotar.called)
            self.assertTrue(any(call.kwargs.get("es_bloqueo") is True for call in mock_rotar.call_args_list))

    @patch("requests.Session.get")
    def test_05_error_conexion_dispara_transient_error(self, mock_get):
        import requests
        from core.domain.exceptions import ScraperTransientError

        mock_get.side_effect = requests.exceptions.ConnectTimeout("Connection timed out (connect timeout=5.0)")

        adapter = DatuarAdapter(delay_min=0.0, delay_max=0.0, use_tor=False)
        adapter.iniciar()

        linea = Linea(ani="1144556677", dni="30112233")
        with self.assertRaises(ScraperTransientError) as ctx:
            adapter.consultar_linea(linea)

        self.assertIn("error de conexión tras", str(ctx.exception))
        self.assertIn("intentos", str(ctx.exception))

    def test_06_timeout_dual_configurado(self):
        adapter = DatuarAdapter(timeout=15, use_tor=False)
        self.assertEqual(adapter.timeout, (5.0, 15.0))

        adapter_tor = DatuarAdapter(use_tor=True)
        self.assertEqual(adapter_tor.timeout, (12.0, 15.0))

        adapter2 = DatuarAdapter(timeout=(4.0, 8.0), use_tor=False)
        self.assertEqual(adapter2.timeout, (4.0, 8.0))

    @patch("adapters.network.proxy_pool.ProxyPoolManager.get_instance")
    def test_07_proxy_pool_integration(self, mock_get_instance):
        mock_ppm = MagicMock()
        mock_ppm.size.return_value = 5
        mock_ppm.get_proxy.return_value = "http://1.2.3.4:8080"
        mock_get_instance.return_value = mock_ppm

        html_datuar = """
        <html>
            <div class="resultado-item"
                 data-nombre-completo="GOMEZ, MARIA LAURA"
                 data-nombre="MARIA LAURA"
                 data-cdu="27351122334"
                 data-edad="38"
                 data-genero="F"
                 data-provincia="CORDOBA"
                 data-ciudad="CORDOBA"
                 data-municipio="CAPITAL">
            </div>
        </html>
        """
        adapter = DatuarAdapter(use_proxy_pool=True, proxy_pool_shared=True)
        self.assertTrue(adapter.use_proxy_pool)
        self.assertFalse(adapter.use_tor)

        adapter.iniciar()
        self.assertTrue(adapter.verificar_salud())

        with patch.object(adapter._proxy_session, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, text=html_datuar)
            linea = Linea(ani="3514556677", dni="35112233")
            res = adapter.consultar_linea(linea)

            self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
            self.assertEqual(res.detalles["origen"], "datuar_proxypool")
            self.assertEqual(res.detalles["nombre_completo"], "GOMEZ, Maria Laura")
            mock_ppm.report_success.assert_called_once_with("http://1.2.3.4:8080")

    def test_08_alias_datuar_fast(self):
        adapter = ScraperRegistry.obtener("datuar_fast")
        self.assertIsInstance(adapter, DatuarAdapter)
        self.assertTrue(adapter.use_proxy_pool)
        self.assertFalse(adapter.use_tor)

    def test_09_tor_stream_isolation(self):
        adapter = DatuarAdapter(use_tor=True, tor_port=9050, delay_min=0.0, delay_max=0.0)
        self.assertTrue(adapter.use_tor)
        self.assertFalse(adapter.use_proxy_pool)

        proxy1 = adapter._generar_proxy_aislado()
        self.assertIn("socks5h://", proxy1)
        self.assertIn("127.0.0.1:9050", proxy1)

        proxy2 = adapter._generar_proxy_aislado(reset=True)
        self.assertNotEqual(proxy1, proxy2)

    @patch("requests.Session.get")
    def test_10_sin_coincidencia_requiere_3_confirmaciones(self, mock_get):
        html_sin_coincidencia = """
        <html>
            <div id="contador-resultados">0 resultados encontrados</div>
        </html>
        """
        mock_resp = MagicMock(status_code=200, text=html_sin_coincidencia)
        mock_get.return_value = mock_resp

        adapter = DatuarAdapter(delay_min=0.0, delay_max=0.0, use_tor=False)
        adapter.iniciar()

        linea = Linea(ani="1144556677", dni="30112233")
        res = adapter.consultar_linea(linea)

        self.assertEqual(res.status, StatusScraping.SIN_COINCIDENCIA)
        self.assertEqual(res.detalles.get("confirmaciones_sin_coincidencia"), 3)
        self.assertEqual(mock_get.call_count, 3)


if __name__ == "__main__":
    unittest.main()




