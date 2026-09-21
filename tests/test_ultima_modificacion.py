# -*- coding: utf-8 -*-
"""
Pruebas Unitarias: Trazabilidad de 'ultima_modificacion' en todos los Scrapers y BD
Verifica:
1. Que ScrapeResult.to_namespace_dict() genere 'ultima_modificacion' para cualquier scraper.
2. Que DatosOrigenEnacom y EnacomBlockAdapter incluyan 'ultima_modificacion' dentro de 'enacom'.
3. Que los formatos de fecha sigan el patrón estándar YYYY-MM-DD HH:MM:SS.
"""
import unittest
import re
from datetime import datetime
from core.domain.entities import ScrapeResult, Titular, Servicio, DatosOrigenEnacom
from core.domain.enums import StatusScraping
from adapters.enacom.enacom_adapter import EnacomBlockAdapter

DATE_REGEX = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"

class TestUltimaModificacion(unittest.TestCase):

    def test_01_all_scrapers_generate_ultima_modificacion(self):
        """Verifica que cualquier scraper genere ultima_modificacion en su namespace."""
        scrapers = ["iris", "claro", "movistar", "personal", "datuar", "cuitonline"]

        for scraper_name in scrapers:
            res = ScrapeResult(
                ani="1134567890",
                status=StatusScraping.COINCIDENCIA,
                fuente_scraper=scraper_name,
                descripcion=f"Prueba de trazabilidad para {scraper_name}"
            )
            payload = res.to_namespace_dict()
            self.assertIn(scraper_name, payload)
            scraper_data = payload[scraper_name]
            self.assertIn("ultima_modificacion", scraper_data, f"Falta 'ultima_modificacion' en {scraper_name}")
            self.assertRegex(
                scraper_data["ultima_modificacion"], 
                DATE_REGEX, 
                f"Formato de fecha inválido en {scraper_name}: {scraper_data['ultima_modificacion']}"
            )

    def test_02_enacom_entity_and_adapter_generate_ultima_modificacion(self):
        """Verifica que el motor ENACOM incluya 'ultima_modificacion' en su diccionario."""
        adapter = EnacomBlockAdapter()
        res_dict = adapter.consultar_bloque_dict("3876858008")
        self.assertIsNotNone(res_dict)
        self.assertIn("ultima_modificacion", res_dict)
        self.assertRegex(res_dict["ultima_modificacion"], DATE_REGEX)

        # Probar to_dict() en DatosOrigenEnacom
        entity = adapter.consultar_bloque("3876858008")
        self.assertIsNotNone(entity)
        entity_dict = entity.to_dict()
        self.assertIn("ultima_modificacion", entity_dict)
        self.assertRegex(entity_dict["ultima_modificacion"], DATE_REGEX)

    def test_03_independent_timestamps_per_scraper(self):
        """Verifica que dos scrapers ejecutados en momentos distintos tengan timestamps independientes."""
        import time
        res_iris = ScrapeResult(ani="1122334455", status=StatusScraping.SIN_COINCIDENCIA, fuente_scraper="iris")
        time.sleep(1.1)
        res_claro = ScrapeResult(ani="1122334455", status=StatusScraping.COINCIDENCIA, fuente_scraper="claro")

        datos_acumulados = {}
        datos_acumulados.update(res_iris.to_namespace_dict())
        datos_acumulados.update(res_claro.to_namespace_dict())

        ts_iris = datos_acumulados["iris"]["ultima_modificacion"]
        ts_claro = datos_acumulados["claro"]["ultima_modificacion"]

        self.assertNotEqual(ts_iris, ts_claro)
        self.assertGreater(ts_claro, ts_iris)

if __name__ == "__main__":
    unittest.main()
