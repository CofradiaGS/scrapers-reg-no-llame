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

    def test_06_cuitonline_solo_exige_dni_y_no_depende_de_datuar(self):
        """CuitOnline solo exige tener DNI disponible y puede ejecutarse sin pasar previamente por Datuar."""
        cadena = ["iris", "cuitonline", "personal"]
        res_iris = ScrapeResult(ani="1122334455", status=StatusScraping.COINCIDENCIA, fuente_scraper="iris")
        
        # Caso A: Con DNI -> Avanza directamente a cuitonline sin exigir datuar
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris,
            cadena=cadena,
            dni_disponible=True,
            fuentes_previas=["iris"]
        )
        self.assertEqual(sig_sc, "cuitonline", "Con DNI debe avanzar a cuitonline sin requerir Datuar")

        # Caso B: Sin DNI -> Saltea cuitonline y pasa al siguiente (personal)
        sig_sc_sin_dni, _ = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris,
            cadena=cadena,
            dni_disponible=False,
            fuentes_previas=["iris"]
        )
        self.assertEqual(sig_sc_sin_dni, "personal", "Sin DNI debe saltar cuitonline y pasar a personal")

    def test_07_telcos_elegibilidad_sin_iris_ni_dni(self):
        """Líneas sin pasar por IRIS y sin DNI deben ser perfectamente elegibles para Telcos."""
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="telcos",
            ani="1159641306",
            dni=None,
            datos_json={}
        )
        self.assertTrue(apto, f"Telcos debe ser apto sin IRIS ni DNI: {msg}")

    def test_08_telcos_elegibilidad_exclusividad_si_hubo_coincidencia_reciente(self):
        """Si Claro dio coincidencia reciente, Telcos no debe re-consultar (exclusividad)."""
        from datetime import datetime
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        datos = {
            "claro": {
                "status": "coincidencia",
                "ultima_modificacion": ahora_str
            }
        }
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="telcos",
            ani="1159641306",
            dni="30111222",
            datos_json=datos
        )
        self.assertFalse(apto, "Telcos no debe ser apto si una operadora ya dio coincidencia reciente")
        self.assertIn("Exclusividad telco", msg)

    def test_09_telcos_resolver_siguiente_etapa_coincidencia_finaliza_completado(self):
        """Si Telcos dio coincidencia, la siguiente etapa debe ser finalizado/completado."""
        res_telcos_match = ScrapeResult(
            ani="1159641306",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="telcos",
            operador="Claro"
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="telcos",
            resultado=res_telcos_match,
            cadena=["telcos"]
        )
        self.assertEqual(sig_sc, "finalizado")
        self.assertEqual(sig_st, EstadoRegistro.COMPLETADO.value)

    def test_10_telcos_resolver_siguiente_etapa_sin_coincidencia_no_va_a_iris(self):
        """Si Telcos dio sin coincidencia, JAMÁS debe retroceder a IRIS ni a otra telco."""
        res_telcos_sin = ScrapeResult(
            ani="1159641306",
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="telcos"
        )
        # 1. Con cadena default (CADENA_DEFAULT sin DNI)
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="telcos",
            resultado=res_telcos_sin,
            cadena=None,
            dni_disponible=False
        )
        self.assertNotEqual(sig_sc, "iris", "Telcos NUNCA debe retroceder a IRIS")
        self.assertNotIn(sig_sc, ReglaPipeline.TELCOS, "Telcos no debe derivar a otra telco")
        self.assertEqual(sig_sc, "finalizado")
        self.assertEqual(sig_st, EstadoRegistro.NO_COINCIDENCIA.value)

        # 2. Con cadena explícita ['telcos'] (como en cola_automatizacion)
        sig_sc2, sig_st2 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="telcos",
            resultado=res_telcos_sin,
            cadena=["telcos"]
        )
        self.assertEqual(sig_sc2, "finalizado")
        self.assertEqual(sig_st2, EstadoRegistro.NO_COINCIDENCIA.value)

    def test_11_salvaguarda_iris_exclusiva_reg_no_llame(self):
        """
        Verifica que la regla 'IRIS primero' aplica estrictamente a queue_registro_no_llame
        y NUNCA interfiere con cola_automatizacion.
        """
        from unittest.mock import MagicMock
        from core.use_cases.process_batch_use_case import ProcesarLoteUseCase
        from core.domain.entities import RegistroCola, Linea

        mock_cola = MagicMock()
        mock_claro = MagicMock()
        mock_claro.nombre = "claro"

        use_case = ProcesarLoteUseCase(cola_repo=mock_cola, scraper_engine=mock_claro)

        # Caso 1: En queue_registro_no_llame, si Claro toma por error una línea de IRIS, debe revertir a IRIS pendiente
        reg_rnl = RegistroCola(
            id=101,
            linea=Linea(ani="1122334455", dni=None),
            scraper_actual="iris",
            tipo_cola="registro_no_llame"
        )
        mock_cola.reservar_lote.return_value = [reg_rnl]
        use_case.ejecutar_lote(batch_size=1)

        persisted = mock_cola.persistir_resultados.call_args[0][0]
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["scraper_actual"], "iris", "En reg_no_llame debe revertir a IRIS")
        self.assertEqual(persisted[0]["estado"], "pendiente")

        # Caso 2: En cola_automatizacion, si Claro saltea por falta de DNI, debe avanzar a Personal (NO a IRIS)
        reg_auto = RegistroCola(
            id=202,
            linea=Linea(ani="1122334455", dni=None),
            scraper_actual="claro",
            tipo_cola="cola_automatizacion"
        )
        mock_cola.reservar_lote.return_value = [reg_auto]
        use_case.ejecutar_lote(batch_size=1)

        persisted2 = mock_cola.persistir_resultados.call_args[0][0]
        self.assertEqual(len(persisted2), 1)
        self.assertEqual(persisted2[0]["scraper_actual"], "personal", "En cola_automatizacion debe avanzar a Personal")
        self.assertEqual(persisted2[0]["estado"], "pendiente")

    def test_12_bcra_requiere_paso_por_datuar_y_cuit(self):
        """Verifica que BCRA exige haber pasado previamente por Datuar y disponer del CUIT/CUIL extraído."""
        # Sin haber pasado por Datuar ni CUIT -> rechaza
        es_apto, motivo = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="bcra",
            ani="1122334455",
            dni=None,
            datos_json={}
        )
        self.assertFalse(es_apto)
        self.assertIn("Datuar", motivo)

        # Solo con DNI pero sin Datuar ni CUIT -> también rechaza
        es_apto2, motivo2 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="bcra",
            ani="1122334455",
            dni="30112233",
            datos_json={}
        )
        self.assertFalse(es_apto2)
        self.assertIn("Datuar", motivo2)

        # Con paso por Datuar y CUIT/CUIL extraído -> es apto
        datos_con_datuar = {
            "datuar": {
                "status": "coincidencia",
                "cuil": "20301122334"
            }
        }
        es_apto3, _ = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="bcra",
            ani="1122334455",
            dni="30112233",
            datos_json=datos_con_datuar
        )
        self.assertTrue(es_apto3)

    def test_13_bcra_flujo_enriquecimiento(self):
        """Verifica que BCRA avanza a finalizado tras completar su etapa en la cadena."""
        cadena = ["iris", "cuitonline", "bcra"]
        res_bcra = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="bcra",
            detalles={"cuit": "20301122334", "deuda_total_pesos": 150000.0}
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="bcra",
            resultado=res_bcra,
            cadena=cadena,
            dni_disponible=True
        )
        self.assertEqual(sig_sc, "finalizado")
        self.assertEqual(sig_st, EstadoRegistro.COMPLETADO.value)

    def test_14_extraer_cuit_y_dni_multinamespace(self):
        """Verifica extracción acumulada de CUIT y DNI a través de namespaces de BCRA, CuitOnline y Datuar."""
        datos = {
            "iris": {"titular": {"nro_documento": "30112233"}},
            "datuar": {"cuil": "20301122334"},
            "bcra": {"cuit": "20301122334", "deuda_total_pesos": 25000.0}
        }
        dni = ReglaPipeline.extraer_dni(datos)
        cuit = ReglaPipeline.extraer_cuit(datos)
        self.assertEqual(dni, "30112233")
        self.assertEqual(cuit, "20301122334")

    def test_15_flujo_condicional_iris_telcos_datuar_cuitonline_bcra(self):
        """
        Verifica las reglas de negocio condicionales:
        - Siempre pasa primero por IRIS
        - Si pasó por IRIS (con o sin coincidencia) avanza a telcos (NO cortocircuita)
        - Si tiene DNI puede pasar por Datuar
        - Si pasó por Datuar puede pasar por CuitOnline
        - Si pasó por CuitOnline puede pasar por BCRA
        """
        # 1. IRIS con coincidencia avanza a TELCOS
        res_iris_match = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="iris",
            titular=Titular(nombre="JUAN PEREZ", nro_documento="30111222")
        )
        sig_sc, sig_st = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_match,
            cadena=None,
            dni_disponible=True
        )
        self.assertEqual(sig_sc, "telcos", "IRIS con coincidencia DEBE avanzar a telcos (sin cortocircuito)")
        self.assertEqual(sig_st, EstadoRegistro.PENDIENTE.value)

        # 2. IRIS sin coincidencia avanza a TELCOS
        res_iris_no_match = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper="iris"
        )
        sig_sc2, sig_st2 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="iris",
            resultado=res_iris_no_match,
            cadena=None,
            dni_disponible=False
        )
        self.assertEqual(sig_sc2, "telcos", "IRIS sin coincidencia DEBE avanzar a telcos")
        self.assertEqual(sig_st2, EstadoRegistro.PENDIENTE.value)

        # 3. TELCOS con DNI avanza a DATUAR
        res_telcos_con_dni = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="telcos",
            titular=Titular(nombre="JUAN PEREZ", nro_documento="30111222")
        )
        sig_sc3, sig_st3 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="telcos",
            resultado=res_telcos_con_dni,
            cadena=None,
            dni_disponible=True,
            fuentes_previas=["iris"]
        )
        self.assertEqual(sig_sc3, "datuar", "Telcos con DNI DEBE avanzar a datuar")
        self.assertEqual(sig_st3, EstadoRegistro.PENDIENTE.value)

        # 4. TELCOS sin DNI NO puede pasar a Datuar -> finaliza
        res_telcos_sin_dni = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="telcos"
        )
        sig_sc4, sig_st4 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="telcos",
            resultado=res_telcos_sin_dni,
            cadena=None,
            dni_disponible=False,
            fuentes_previas=["iris"]
        )
        self.assertEqual(sig_sc4, "finalizado", "Telcos sin DNI no puede pasar a datuar, debe finalizar")
        self.assertEqual(sig_st4, EstadoRegistro.COMPLETADO.value)

        # 5. DATUAR avanza a CUITONLINE
        res_datuar = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="datuar",
            detalles={"cuil": "20301112224"}
        )
        sig_sc5, sig_st5 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="datuar",
            resultado=res_datuar,
            cadena=None,
            dni_disponible=True,
            fuentes_previas=["iris", "telcos"]
        )
        self.assertEqual(sig_sc5, "cuitonline", "Si pasó por Datuar DEBE avanzar a cuitonline")
        self.assertEqual(sig_st5, EstadoRegistro.PENDIENTE.value)

        # 6. CUITONLINE avanza a BCRA
        res_cuit = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="cuitonline",
            detalles={"cuit_limpio": "20301112224"}
        )
        sig_sc6, sig_st6 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="cuitonline",
            resultado=res_cuit,
            cadena=None,
            dni_disponible=True,
            fuentes_previas=["iris", "telcos", "datuar"]
        )
        self.assertEqual(sig_sc6, "bcra", "Si pasó por CuitOnline DEBE avanzar a bcra")
        self.assertEqual(sig_st6, EstadoRegistro.PENDIENTE.value)

        # 7. BCRA finaliza
        res_bcra = ScrapeResult(
            ani="1122334455",
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper="bcra"
        )
        sig_sc7, sig_st7 = ReglaPipeline.resolver_siguiente_etapa(
            scraper_actual="bcra",
            resultado=res_bcra,
            cadena=None,
            dni_disponible=True,
            fuentes_previas=["iris", "telcos", "datuar", "cuitonline"]
        )
        self.assertEqual(sig_sc7, "finalizado", "BCRA finaliza el pipeline")
        self.assertEqual(sig_st7, EstadoRegistro.COMPLETADO.value)

if __name__ == "__main__":
    unittest.main()

