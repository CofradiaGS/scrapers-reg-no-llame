# -*- coding: utf-8 -*-
"""
Pruebas Unitarias: ReglaPipeline - Lógica Condicional, Requisitos de DNI y Precedencias
Verifica:
1. Camino sin DNI: Salto automático de Claro, Datuar y CuitOnline hacia Personal/Movistar.
2. Si Personal o Movistar coinciden sin DNI: Finalización directa sin pasar por Datuar.
3. Camino con DNI: Posibilidad de ejecutar en cualquier orden configurado.
4. Cortocircuito de Telcos: Al coincidir Claro, Personal o Movistar, no se consultan las demás telcos.
5. Regla de Identidad: CuitOnline nunca se ejecuta si no pasó por Datuar.
"""
import unittest
from core.domain.entities import ReglaPipeline, ScrapeResult, Titular
from core.domain.enums import StatusScraping, EstadoRegistro

class TestReglaPipeline(unittest.TestCase):

    def setUp(self):
        self.cadena_telco_first = ["iris", "claro", "personal", "movistar", "datuar", "cuitonline"]
        self.cadena_identidad_first = ["iris", "datuar", "cuitonline", "claro", "personal", "movistar"]

    def test_01_sin_dni_saltea_claro_datuar_cuitonline_directo_a_personal(self):
        """Si IRIS no tiene DNI, debe saltear Claro, Datuar y CuitOnline e ir directo a Personal."""
        res_iris_sin_dni = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="iris"
        )
        # Probamos con cadena telco primero
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_sin_dni,
            cadena=self.cadena_telco_first,
            dni_disponible=False
        )
        self.assertEqual(sig_sc, "personal", "Debió saltear Claro e ir directo a Personal por falta de DNI")
        self.assertEqual(sig_st, EstadoRegistro.PENDIENTE.value)

        # Probamos con cadena identidad primero
        sig_sc2, sig_st2 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_sin_dni,
            cadena=self.cadena_identidad_first,
            dni_disponible=False
        )
        self.assertEqual(sig_sc2, "personal", "Debió saltear Datuar, CuitOnline y Claro e ir directo a Personal")
        self.assertEqual(sig_st2, EstadoRegistro.PENDIENTE.value)

    def test_02_sin_dni_personal_coincidencia_finaliza_directo(self):
        """Si Personal coincide sin DNI, no puede pasar a Datuar ni Movistar: debe finalizar completado."""
        res_personal_match = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="personal",
            operador="Personal"
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="personal",
            resultado=res_personal_match,
            cadena=self.cadena_telco_first,
            dni_disponible=False
        )
        self.assertEqual(sig_sc, "finalizado")
        self.assertEqual(sig_st, EstadoRegistro.COMPLETADO.value)

    def test_03_sin_dni_personal_no_match_avanza_a_movistar(self):
        """Si Personal no coincide y no hay DNI, avanza a Movistar."""
        res_personal_no_match = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="personal"
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="personal",
            resultado=res_personal_no_match,
            cadena=self.cadena_telco_first,
            dni_disponible=False
        )
        self.assertEqual(sig_sc, "movistar")
        self.assertEqual(sig_st, EstadoRegistro.PENDIENTE.value)

    def test_04_con_dni_telco_first_flujo_completo(self):
        """Con DNI desde IRIS, avanza a Claro; si Claro coincide, saltea telcos y pasa a Datuar."""
        res_iris_con_dni = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="iris",
            titular=Titular(nombre="JUAN", nro_documento="28123456")
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_con_dni,
            cadena=self.cadena_telco_first,
            dni_disponible=True
        )
        self.assertEqual(sig_sc, "claro", "Con DNI debe poder pasar por Claro")

        # Claro coincide
        res_claro_match = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="claro",
            operador="Claro"
        )
        sig_sc2, sig_st2 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="claro",
            resultado=res_claro_match,
            cadena=self.cadena_telco_first,
            dni_disponible=True
        )
        self.assertEqual(sig_sc2, "datuar", "Claro coincidió: debe cortocircuitar Personal/Movistar e ir a Datuar")

        # Datuar procesa
        res_datuar = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="datuar"
        )
        sig_sc3, sig_st3 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="datuar",
            resultado=res_datuar,
            cadena=self.cadena_telco_first,
            dni_disponible=True,
            fuentes_previas=["iris", "claro"],
            coincidencia_telco_previa=True
        )
        self.assertEqual(sig_sc3, "cuitonline", "Datuar debe derivar obligatoriamente a CuitOnline")

        # CuitOnline procesa
        res_cuit = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="cuitonline"
        )
        sig_sc4, sig_st4 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="cuitonline",
            resultado=res_cuit,
            cadena=self.cadena_telco_first,
            dni_disponible=True,
            fuentes_previas=["iris", "claro", "datuar"],
            coincidencia_telco_previa=True
        )
        self.assertEqual(sig_sc4, "finalizado")
        self.assertEqual(sig_st4, EstadoRegistro.COMPLETADO.value)

    def test_05_con_dni_identidad_first_flujo(self):
        """Si el usuario configura identidad primero, se ejecutan Datuar y CuitOnline antes de Claro."""
        res_iris_con_dni = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="iris",
            titular=Titular(nombre="MARIA", nro_documento="30111222")
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_con_dni,
            cadena=self.cadena_identidad_first,
            dni_disponible=True
        )
        self.assertEqual(sig_sc, "datuar", "Identidad primero: debe avanzar a Datuar")

        # Datuar avanza a CuitOnline
        res_dat = ScrapeResult(ani="1122334455", status=StatusScraping.COINCIDENCIA, fuente_scraper="datuar")
        sig_sc2, sig_st2 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="datuar",
            resultado=res_dat,
            cadena=self.cadena_identidad_first,
            dni_disponible=True,
            fuentes_previas=["iris"]
        )
        self.assertEqual(sig_sc2, "cuitonline")

        # CuitOnline avanza a Claro
        res_cuit = ScrapeResult(ani="1122334455", status=StatusScraping.COINCIDENCIA, fuente_scraper="cuitonline")
        sig_sc3, sig_st3 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="cuitonline",
            resultado=res_cuit,
            cadena=self.cadena_identidad_first,
            dni_disponible=True,
            fuentes_previas=["iris", "datuar"]
        )
        self.assertEqual(sig_sc3, "claro")

    def test_06_cuitonline_no_puede_ejecutarse_sin_datuar(self):
        """CuitOnline no puede ejecutarse si no pasó previamente por Datuar."""
        cadena_invalida = ["iris", "cuitonline", "personal"]
        res_iris = ScrapeResult(ani="1122334455", status=StatusScraping.COINCIDENCIA, fuente_scraper="iris")
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris,
            cadena=cadena_invalida,
            dni_disponible=True,
            fuentes_previas=["iris"]
        )
        # Debe saltar cuitonline y pasar a personal
        self.assertEqual(sig_sc, "personal", "Debió saltar cuitonline porque no se pasó por datuar")

if __name__ == "__main__":
    unittest.main()
