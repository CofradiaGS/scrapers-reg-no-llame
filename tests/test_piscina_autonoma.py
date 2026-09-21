# -*- coding: utf-8 -*-
"""
Pruebas Unitarias: Modelo de Piscina Autónoma Multi-PC con TTL de 7 Días
Verifica:
1. PC Movistar: 'de nadie', solo necesita ANI y que haya pasado por Personal en los últimos 7 días.
2. PC Movistar: rechaza registros si Personal corrió hace > 7 días (stale) o si Claro/Personal coincidieron.
3. PC Personal: línea sin DNI entra libre directo; línea con DNI exige Claro en los últimos 7 días.
4. PC CuitOnline: exige haber pasado por Datuar en los últimos 7 días.
5. Concurrencia Anti-Colisión: registros en 'procesando' nunca son reclamados por ninguna PC.
6. Helper es_reciente: cálculo preciso de ventana de 7 días.
"""
import unittest
from datetime import datetime, timedelta
from core.domain.entities import ReglaPipeline
from adapters.db.memory_adapter import MemoryQueueAdapter

class TestPiscinaAutonoma(unittest.TestCase):

    def setUp(self):
        self.ahora = datetime.now()
        self.hace_2_dias = (self.ahora - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        self.hace_5_dias = (self.ahora - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.hace_10_dias = (self.ahora - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")

    def test_01_helper_es_reciente(self):
        """Verifica el cálculo de frescura temporal con la ventana de 7 días."""
        self.assertTrue(ReglaPipeline.es_reciente(self.hace_2_dias, max_dias=7))
        self.assertTrue(ReglaPipeline.es_reciente(self.hace_5_dias, max_dias=7))
        self.assertFalse(ReglaPipeline.es_reciente(self.hace_10_dias, max_dias=7))
        self.assertFalse(ReglaPipeline.es_reciente(None, max_dias=7))
        self.assertFalse(ReglaPipeline.es_reciente("fecha_invalida", max_dias=7))

    def test_02_pc_movistar_de_nadie_solo_necesita_ani_y_personal_reciente(self):
        """
        PC Movistar: No requiere titular ni DNI ('de nadie', solo ANI),
        pero EXIGE haber pasado por Personal en los últimos 7 días.
        """
        # Caso A: Línea sin DNI, pasó por Personal hace 2 días -> APTO
        datos_personal_ok = {
            "personal": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_2_dias
            }
        }
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="movistar",
            ani="1122334455",
            dni=None,
            datos_json=datos_personal_ok,
            dias_validez=7
        )
        self.assertTrue(apto, f"Movistar debió aceptar la línea sin DNI con Personal reciente: {msg}")

        # Caso B: Línea sin DNI, pero Personal corrió hace 10 días -> RECHAZADO (Personal caducado)
        datos_personal_viejo = {
            "personal": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_10_dias
            }
        }
        apto2, msg2 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="movistar",
            ani="1122334455",
            dni=None,
            datos_json=datos_personal_viejo,
            dias_validez=7
        )
        self.assertFalse(apto2, "Movistar debió rechazar la línea porque Personal superó los 7 días")

        # Caso C: Línea que nunca pasó por Personal -> RECHAZADO
        apto3, msg3 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="movistar",
            ani="1122334455",
            dni=None,
            datos_json={},
            dias_validez=7
        )
        self.assertFalse(apto3, "Movistar debió rechazar la línea que nunca pasó por Personal")

    def test_03_pc_movistar_bloqueado_por_exclusividad_telco(self):
        """Si Claro o Personal dieron coincidencia en los últimos 7 días, Movistar no puede tocarla."""
        datos_claro_coincide = {
            "claro": {
                "status": "coincidencia",
                "ultima_modificacion": self.hace_2_dias
            },
            "personal": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_2_dias
            }
        }
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="movistar",
            ani="1122334455",
            dni=None,
            datos_json=datos_claro_coincide,
            dias_validez=7
        )
        self.assertFalse(apto, "Movistar no debe reclamar una línea con coincidencia activa en Claro")

    def test_04_pc_personal_sin_dni_entra_directo(self):
        """PC Personal: si la línea NO tiene DNI, entra libre directo sin esperar a Claro."""
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="personal",
            ani="1122334455",
            dni=None,
            datos_json={},
            dias_validez=7
        )
        self.assertTrue(apto, f"Personal debió aceptar línea sin DNI directo: {msg}")

    def test_05_pc_personal_con_dni_exige_claro_en_ultimos_7_dias(self):
        """PC Personal: si la línea TIENE DNI, exige haber pasado por Claro en los últimos 7 días."""
        # Caso A: Con DNI pero sin Claro previo -> RECHAZADO
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="personal",
            ani="1122334455",
            dni="20123456",
            datos_json={},
            dias_validez=7
        )
        self.assertFalse(apto, "Personal con DNI debió rechazar la línea porque no pasó por Claro")

        # Caso B: Con DNI y Claro hace 10 días -> RECHAZADO (Claro caducó)
        datos_claro_viejo = {
            "claro": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_10_dias
            }
        }
        apto2, msg2 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="personal",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_claro_viejo,
            dias_validez=7
        )
        self.assertFalse(apto2, "Personal debió rechazar la línea porque Claro caducó (> 7 días)")

        # Caso C: Con DNI y Claro hace 2 días -> APTO
        datos_claro_ok = {
            "claro": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_2_dias
            }
        }
        apto3, msg3 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="personal",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_claro_ok,
            dias_validez=7
        )
        self.assertTrue(apto3, f"Personal con DNI y Claro reciente debió ser apto: {msg3}")

    def test_06_pc_cuitonline_no_tiene_regla_7_dias_solo_exige_datuar_presente(self):
        """PC CuitOnline: NO tiene regla de 7 días. Solo exige DNI y que Datuar haya sido ejecutado."""
        # Caso A: Con DNI y Datuar hace 30 días (sin restricción de 7 días) -> APTO
        hace_30_dias = (self.ahora - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        datos_datuar_30d = {
            "datuar": {
                "status": "coincidencia",
                "ultima_modificacion": hace_30_dias,
                "dni": "20123456"
            }
        }
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="cuitonline",
            ani="1122334455",
            dni=None,
            datos_json=datos_datuar_30d,
            dias_validez=7
        )
        self.assertTrue(apto, f"CuitOnline debió ser apto aunque Datuar tenga 30 días: {msg}")

        # Caso B: Sin Datuar previo -> RECHAZADO
        apto2, msg2 = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="cuitonline",
            ani="1122334455",
            dni="20123456",
            datos_json={},
            dias_validez=7
        )
        self.assertFalse(apto2, "CuitOnline debió rechazar si Datuar nunca se ejecutó")

    def test_07_no_telcos_solo_nutren_los_que_no_tienen_datos(self):
        """Los motores no-telco (iris, datuar, cuitonline) no se re-ejecutan si ya tienen datos."""
        # Datuar ya tiene datos -> No re-ejecutar
        datos_con_datuar = {
            "datuar": {
                "status": "coincidencia",
                "dni": "20123456"
            }
        }
        apto_datuar, msg_d = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="datuar",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_con_datuar
        )
        self.assertFalse(apto_datuar, "Datuar no debe re-ejecutarse si ya posee datos")

        # CuitOnline ya tiene datos -> No re-ejecutar
        datos_con_cuit = {
            "datuar": {"status": "coincidencia", "dni": "20123456"},
            "cuitonline": {"status": "coincidencia", "cuit": "20-20123456-0"}
        }
        apto_cuit, msg_c = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="cuitonline",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_con_cuit
        )
        self.assertFalse(apto_cuit, "CuitOnline no debe re-ejecutarse si ya posee datos")

    def test_08_telcos_pueden_re_ejecutarse_a_futuro_si_pasaron_mas_de_7_dias(self):
        """Las telcos (Claro, Personal, Movistar) pueden volver a pasar si transcurrieron más de 7 días."""
        # Claro corrió hace 10 días -> A futuro se puede volver a pasar el scraper
        datos_claro_10d = {
            "claro": {
                "status": "sin_coincidencia",
                "ultima_modificacion": self.hace_10_dias
            }
        }
        apto, msg = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="claro",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_claro_10d,
            dias_validez=7
        )
        self.assertTrue(apto, f"Claro debió poder re-ejecutarse a futuro tras 10 días: {msg}")

    def test_09_ninguna_pc_puede_agarrar_registro_en_procesando(self):
        """Ninguna PC puede reclamar un registro con estado='procesando'."""
        adapter = MemoryQueueAdapter(initial_records=[
            {
                "id": 100,
                "ani": 1122334455,
                "scraper_actual": "movistar",
                "estado": "procesando",
                "datos_json": {
                    "personal": {
                        "status": "sin_coincidencia",
                        "ultima_modificacion": self.hace_2_dias
                    }
                }
            },
            {
                "id": 101,
                "ani": 1133445566,
                "scraper_actual": "movistar",
                "estado": "pendiente",
                "datos_json": {
                    "personal": {
                        "status": "sin_coincidencia",
                        "ultima_modificacion": self.hace_2_dias
                    }
                }
            }
        ])

        lote = adapter.reservar_lote(batch_size=10, scraper_nombre="movistar")
        self.assertEqual(len(lote), 1, "Solo debe reclamar el registro en 'pendiente'")
        self.assertEqual(lote[0].id, 101, "No debe tocar el ID 100 en 'procesando'")

    def test_10_flag_solo_sin_coincidencia_rechaza_coincidencias_historicas_en_cualquiera_de_las_3(self):
        """
        Con solo_sin_coincidencia=True, si cualquiera de las 3 compañías (o la raíz)
        tuvo coincidencia en el pasado (incluso > 7 días), el registro es rechazado.
        """
        hace_15_dias = (self.ahora - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Coincidencia previa en Claro hace 15 días
        datos_claro = {
            "claro": {"status": "coincidencia", "ultima_modificacion": hace_15_dias}
        }
        for sc in ["claro", "personal", "movistar"]:
            apto, msg = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre=sc,
                ani="1122334455",
                dni="20123456",
                datos_json=datos_claro,
                solo_sin_coincidencia=True
            )
            self.assertFalse(apto, f"{sc} no debe tomar registro con coincidencia en Claro (flag activa): {msg}")

        # 2. Coincidencia previa en Personal hace 20 días
        hace_20_dias = (self.ahora - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
        datos_personal = {
            "personal": {"status": "coincidencia", "ultima_modificacion": hace_20_dias}
        }
        for sc in ["claro", "personal", "movistar"]:
            apto, msg = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre=sc,
                ani="1122334455",
                dni="20123456",
                datos_json=datos_personal,
                solo_sin_coincidencia=True
            )
            self.assertFalse(apto, f"{sc} no debe tomar registro con coincidencia en Personal (flag activa): {msg}")

        # 3. Coincidencia previa en Movistar
        datos_movistar = {
            "movistar": {"status": "coincidencia", "ultima_modificacion": self.hace_2_dias}
        }
        for sc in ["claro", "personal", "movistar"]:
            apto, msg = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre=sc,
                ani="1122334455",
                dni="20123456",
                datos_json=datos_movistar,
                solo_sin_coincidencia=True
            )
            self.assertFalse(apto, f"{sc} no debe tomar registro con coincidencia en Movistar (flag activa): {msg}")

        # 4. Coincidencia previa en raíz (formato legado)
        datos_raiz = {"status": "coincidencia", "operador": "Claro"}
        for sc in ["claro", "personal", "movistar"]:
            apto, msg = ReglaPipeline.es_elegible_para_scraper(
                scraper_nombre=sc,
                ani="1122334455",
                dni="20123456",
                datos_json=datos_raiz,
                solo_sin_coincidencia=True
            )
            self.assertFalse(apto, f"{sc} no debe tomar registro con coincidencia en raíz: {msg}")

    def test_11_flag_solo_sin_coincidencia_acepta_lineas_virgenes_o_con_sin_coincidencia(self):
        """
        Con solo_sin_coincidencia=True, si ninguna de las 3 compañías tuvo coincidencia,
        las líneas que cumplen los requisitos estándar son aceptadas.
        """
        # Caso A: Línea virgen con DNI -> Apta para Claro
        apto_claro, msg_c = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="claro",
            ani="1122334455",
            dni="20123456",
            datos_json={},
            solo_sin_coincidencia=True
        )
        self.assertTrue(apto_claro, f"Claro debió aceptar línea virgen con DNI: {msg_c}")

        # Caso B: Claro dio sin_coincidencia reciente -> Personal la acepta
        datos_claro_sin = {
            "claro": {"status": "sin_coincidencia", "ultima_modificacion": self.hace_2_dias}
        }
        apto_pers, msg_p = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="personal",
            ani="1122334455",
            dni="20123456",
            datos_json=datos_claro_sin,
            solo_sin_coincidencia=True
        )
        self.assertTrue(apto_pers, f"Personal debió aceptar línea con Claro sin coincidencia: {msg_p}")

        # Caso C: Personal dio sin_coincidencia reciente -> Movistar la acepta
        datos_pers_sin = {
            "personal": {"status": "sin_coincidencia", "ultima_modificacion": self.hace_2_dias}
        }
        apto_mov, msg_m = ReglaPipeline.es_elegible_para_scraper(
            scraper_nombre="movistar",
            ani="1122334455",
            dni=None,
            datos_json=datos_pers_sin,
            solo_sin_coincidencia=True
        )
        self.assertTrue(apto_mov, f"Movistar debió aceptar línea con Personal sin coincidencia: {msg_m}")

    def test_12_reservar_lote_con_solo_sin_coincidencia_en_adapter(self):
        """Verifica que el repositorio en memoria aplique solo_sin_coincidencia al reservar lote."""
        adapter = MemoryQueueAdapter(initial_records=[
            {
                "id": 201,
                "ani": 1111111111,
                "scraper_actual": "movistar",
                "estado": "pendiente",
                "datos_json": {
                    "personal": {"status": "coincidencia", "ultima_modificacion": self.hace_2_dias}
                }
            },
            {
                "id": 202,
                "ani": 2222222222,
                "scraper_actual": "movistar",
                "estado": "pendiente",
                "datos_json": {
                    "personal": {"status": "sin_coincidencia", "ultima_modificacion": self.hace_2_dias}
                }
            },
            {
                "id": 203,
                "ani": 3333333333,
                "scraper_actual": "movistar",
                "estado": "pendiente",
                "datos_json": {
                    "status": "coincidencia"
                }
            }
        ])

        # Con solo_sin_coincidencia=True, solo el ID 202 debe ser reservado
        lote = adapter.reservar_lote(batch_size=10, scraper_nombre="movistar", solo_sin_coincidencia=True)
        self.assertEqual(len(lote), 1)
        self.assertEqual(lote[0].id, 202)

if __name__ == "__main__":
    unittest.main()
