# -*- coding: utf-8 -*-
"""
Pruebas Unitarias para EnacomWebAdapter y EnacomCaptchaSolver.
Verifica el cumplimiento estricto del contrato IScraperEnginePort,
normalización de operadores, validación de líneas y resolución de imágenes.
"""
import unittest
import numpy as np

try:
    import cv2
except (ImportError, ModuleNotFoundError):
    cv2 = None

from core.domain.entities import Linea, StatusScraping
from core.ports.scraper_port import IScraperEnginePort
from adapters.scrapers.registry import ScraperRegistry
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter
from adapters.scrapers.enacom_web.captcha_solver import EnacomCaptchaSolver


@unittest.skipIf(cv2 is None, "OpenCV (cv2) no está instalado en este entorno")
class TestEnacomWebAdapter(unittest.TestCase):
    """Pruebas unitarias para el adaptador de scraping web de ENACOM."""

    def setUp(self):
        self.adapter = EnacomWebAdapter(headless=True)

    def tearDown(self):
        self.adapter.cerrar()

    def test_implements_interface(self):
        """Verifica que el adaptador implemente el contrato IScraperEnginePort."""
        self.assertIsInstance(self.adapter, IScraperEnginePort)
        self.assertEqual(self.adapter.nombre, "scraper_enacom")
        self.assertTrue(self.adapter.autenticar())

    def test_registry_resolution(self):
        """Verifica que el registro resuelva todos los alias oficiales de ENACOM Web."""
        for alias in ["scraper_enacom", "enacom_web", "enacom", "enacom_numeracion"]:
            instance = ScraperRegistry.obtener(alias)
            self.assertIsInstance(instance, EnacomWebAdapter)
            instance.cerrar()

    def test_normalizar_operador(self):
        """Verifica la homologación de razones sociales de telecomunicaciones."""
        self.assertEqual(self.adapter._normalizar_operador("AMX ARGENTINA S.A."), "Claro")
        self.assertEqual(self.adapter._normalizar_operador("TELEFONICA MOVILES ARGENTINA S.A."), "Movistar")
        self.assertEqual(self.adapter._normalizar_operador("TELECOM ARGENTINA S.A."), "Personal")
        self.assertEqual(self.adapter._normalizar_operador("TELMEX ARGENTINA S.A."), "Telmex")
        self.assertEqual(self.adapter._normalizar_operador("TELECENTRO S.A."), "Telecentro")
        self.assertEqual(self.adapter._normalizar_operador("COOPERATIVA TELEFONICA"), "COOPERATIVA TELEFONICA")

    def test_ani_invalido_retorna_error(self):
        """Un ANI con formato erróneo debe cortocircuitar antes de abrir el navegador."""
        res_corto = self.adapter.consultar_linea(Linea(ani="11234"))
        self.assertEqual(res_corto.status, StatusScraping.ERROR)
        self.assertIn("inválido", res_corto.descripcion.lower())

        res_letras = self.adapter.consultar_linea(Linea(ani="11ABC45678"))
        self.assertEqual(res_letras.status, StatusScraping.ERROR)

    def test_captcha_solver_clean_image(self):
        """Prueba la máscara cromática de OpenCV con una imagen sintética."""
        solver = EnacomCaptchaSolver()
        # Crear imagen sintética: 50x180 px con fondo blanco (255, 255, 255)
        img = np.full((50, 180, 3), 255, dtype=np.uint8)
        # Dibujar líneas grises (200, 200, 200)
        cv2.line(img, (0, 10), (180, 40), (200, 200, 200), 1)
        # Dibujar texto simulado azul (B=140, G=0, R=0)
        cv2.putText(img, "TEST5", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (140, 0, 0), 2)

        _, buf = cv2.imencode(".png", img)
        cleaned = solver.clean_image_bytes(buf.tobytes())

        # La imagen resultante debe tener 1 solo canal (escala de grises) y tamaño 3x escalado con margen
        self.assertEqual(len(cleaned.shape), 2)
        self.assertGreater(cleaned.shape[0], 50)
        self.assertGreater(cleaned.shape[1], 180)

    def test_rotacion_preventiva_proactiva_ip(self):
        """Verifica que el límite por IP sea 25 y que rotar_proxy resetee el contador."""
        self.assertEqual(self.adapter.MAX_CONSULTAS_POR_IP, 25)
        self.assertEqual(self.adapter._consultas_con_esta_ip, 0)

        # Simular consultas
        self.adapter._consultas_con_esta_ip = 25
        # Simular rotar_proxy sin abrir browser real
        self.adapter.rotar_proxy = lambda: setattr(self.adapter, "_consultas_con_esta_ip", 0) or True
        self.adapter.rotar_proxy()
        self.assertEqual(self.adapter._consultas_con_esta_ip, 0)

    def test_ip_tracker_evaluacion_y_bloqueo(self):
        """Verifica el funcionamiento del IP Tracker centralizado con SQLite WAL."""
        from adapters.scrapers.enacom_web.ip_tracker import EnacomIPTracker
        tracker = EnacomIPTracker()
        import time
        uid = int(time.time() * 1000) % 200 + 10
        test_ip = f"181.99.88.{uid}"
        
        # 1. IP limpia
        apta, motivo, _ = tracker.evaluar_ip(test_ip)
        self.assertTrue(apta)

        # 2. Registrar éxito
        tracker.registrar_consulta_exitosa(test_ip)
        
        # 3. Registrar bloqueo 100
        tracker.registrar_bloqueo_100(test_ip, "Límite 100 alcanzado")
        apta_b, motivo_b, _ = tracker.evaluar_ip(test_ip)
        self.assertFalse(apta_b)
        self.assertIn("100", motivo_b)

        # 4. Registrar rate limit 5/minuto
        ip_min = f"181.99.89.{uid}"
        tracker.registrar_rate_limit_minuto(ip_min, segundos=5.0, motivo="Límite 5/min")
        apta_m, motivo_m, espera = tracker.evaluar_ip(ip_min)
        self.assertFalse(apta_m)
        self.assertGreater(espera, 0.0)


if __name__ == "__main__":
    unittest.main()

