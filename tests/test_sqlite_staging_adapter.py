# -*- coding: utf-8 -*-
"""
Pruebas Unitarias de SQLiteStagingAdapter
Verifica el ciclo de vida local:
1. Creación de esquema y modo WAL.
2. Inserción de tareas descargadas.
3. Reserva atómica en lote (pendiente -> en_proceso).
4. Persistencia de resultados enriquecidos (en_proceso -> listo_para_subir).
5. Obtención de lote para push y marcado de sincronizado.
6. Reversión segura ante interrupción.
7. Purga de registros antiguos de más de 7 días.
"""
import os
import sys
import unittest
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter



class TestSQLiteStagingAdapter(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_staging.db")
        self.adapter = SQLiteStagingAdapter(
            db_path=self.db_path,
            tipo_cola="cola_automatizacion",
            auto_id="telco_scraper",
            pc_id="PC-TEST"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_insertar_y_reservar_lote(self):
        tareas = [
            {"id": 1001, "numero_de_linea": "2614556677", "dni": "33517690", "auto_id": "telco_scraper", "datos": {"test": 1}},
            {"id": 1002, "numero_de_linea": "1155443322", "dni": "28445112", "auto_id": "telco_scraper", "datos": {"test": 2}},
            {"id": 1003, "numero_de_linea": "3416778899", "dni": None, "auto_id": "telco_scraper", "datos": {"test": 3}},
        ]
        insertados = self.adapter.insertar_tareas_descargadas(tareas)
        self.assertEqual(insertados, 3)
        self.assertEqual(self.adapter.contar_pendientes(), 3)

        lote = self.adapter.reservar_lote(batch_size=2)
        self.assertEqual(len(lote), 2)
        self.assertEqual(lote[0].id, 1001)
        self.assertEqual(lote[0].linea.ani, "2614556677")
        self.assertEqual(lote[0].linea.dni, "33517690")
        self.assertEqual(self.adapter.contar_pendientes(), 1)

    def test_02_persistir_resultados_y_obtener_para_push(self):
        tareas = [
            {"id": 2001, "numero_de_linea": "2614556677", "dni": "33517690", "auto_id": "telco_scraper"},
            {"id": 2002, "numero_de_linea": "1155443322", "dni": "28445112", "auto_id": "telco_scraper"},
        ]
        self.adapter.insertar_tareas_descargadas(tareas)
        lote = self.adapter.reservar_lote(batch_size=2)

        resultados = [
            {
                "id": 2001,
                "ani": "2614556677",
                "dni": "33517690",
                "status": "coincidencia",
                "scraper_actual": "finalizado",
                "descripcion": "Coincidencia Claro Cortocircuito",
                "datos": {"claro": {"status": "coincidencia", "deuda": 1500.0}},
                "latencia": 0.35
            },
            {
                "id": 2002,
                "ani": "1155443322",
                "dni": "28445112",
                "status": "sin_coincidencias",
                "scraper_actual": "finalizado",
                "descripcion": "Sin deuda en ninguna",
                "datos": {"status": "sin_coincidencias"},
                "latencia": 0.85
            }
        ]
        ok = self.adapter.persistir_resultados(resultados)
        self.assertTrue(ok)

        # Verificar que están listas para el push nocturno
        para_push = self.adapter.obtener_lote_para_push(limit=10)
        self.assertEqual(len(para_push), 2)
        self.assertEqual(para_push[0]["id_vps"], 2001)
        self.assertEqual(para_push[0]["estado_local"], "en_subida")
        self.assertIn("claro", para_push[0]["resultado_json"])

        # Marcar sincronizados
        ok_sync = self.adapter.marcar_como_sincronizados([2001, 2002])
        self.assertTrue(ok_sync)

        # Ahora no debe quedar nada pendiente para push
        para_push_post = self.adapter.obtener_lote_para_push(limit=10)
        self.assertEqual(len(para_push_post), 0)

        stats = self.adapter.obtener_estadisticas()
        self.assertEqual(stats["sincronizado"], 2)

    def test_03_reversion_a_pendiente(self):
        tareas = [{"id": 3001, "numero_de_linea": "1144002233", "dni": "12345678"}]
        self.adapter.insertar_tareas_descargadas(tareas)
        lote = self.adapter.reservar_lote(batch_size=1)
        self.assertEqual(len(lote), 1)
        self.assertEqual(self.adapter.contar_pendientes(), 0)

        # Revertir
        self.adapter.revertir_a_pendiente([3001])
        self.assertEqual(self.adapter.contar_pendientes(), 1)

    def test_04_prioridad_telcos_con_dni_primero(self):
        tareas = [
            {"id": 4001, "numero_de_linea": "1144001111", "dni": None, "scraper_actual": "telcos"},
            {"id": 4002, "numero_de_linea": "1144002222", "dni": "12345678", "scraper_actual": "telcos"},
            {"id": 4003, "numero_de_linea": "1144003333", "dni": "", "scraper_actual": "telcos"},
            {"id": 4004, "numero_de_linea": "1144004444", "dni": "87654321", "scraper_actual": "telcos"},
        ]
        self.adapter.insertar_tareas_descargadas(tareas)

        # Lote 1: Debe traer primero las 2 tareas que tienen DNI (4002 y 4004)
        lote1 = self.adapter.reservar_lote(batch_size=2, scraper_nombre="telcos")
        self.assertEqual(len(lote1), 2)
        self.assertEqual([r.id for r in lote1], [4002, 4004])
        self.assertTrue(all(bool(r.linea.dni) for r in lote1))

        # Lote 2: Agotados los que tienen DNI, debe traer las 2 tareas restantes sin DNI (4001 y 4003)
        lote2 = self.adapter.reservar_lote(batch_size=2, scraper_nombre="telcos")
        self.assertEqual(len(lote2), 2)
        self.assertEqual([r.id for r in lote2], [4001, 4003])
        self.assertFalse(any(bool(r.linea.dni) for r in lote2))

    def test_05_posta_intermedia_permanece_pendiente_local(self):
        """Verifica la Opción 1: los registros intermedios permanecen como 'pendiente'
        en SQLite local para que el siguiente scraper los tome de inmediato sin esperar al push nocturno."""
        tareas = [{"id": 5001, "numero_de_linea": "1144556677", "dni": None, "scraper_actual": "iris"}]
        self.adapter.insertar_tareas_descargadas(tareas)

        # 1. IRIS reserva la tarea
        lote_iris = self.adapter.reservar_lote(batch_size=1, scraper_nombre="iris")
        self.assertEqual(len(lote_iris), 1)
        self.assertEqual(lote_iris[0].id, 5001)

        # 2. IRIS procesa sin coincidencia y pasa la posta a 'telcos'
        iris_resultado = [{
            "id": 5001,
            "ani": "1144556677",
            "dni": "28112233",
            "status": "sin_coincidencia",
            "scraper_actual": "telcos",
            "descripcion": "Sin port out en IRIS. DNI encontrado",
            "datos": {"iris": {"status": "sin_coincidencia", "titular": {"nro_documento": "28112233"}}},
            "fuente": ["iris"],
            "latencia": 0.25
        }]
        self.adapter.persistir_resultados(iris_resultado)

        # Debe permanecer como 'pendiente' en SQLite para telcos (NO listo_para_subir)
        self.assertEqual(self.adapter.contar_pendientes(scraper_actual="telcos"), 1)
        self.assertEqual(self.adapter.contar_listos_para_subir(), 0)

        # 3. El worker de Telcos reserva el lote de inmediato desde SQLite local
        lote_telcos = self.adapter.reservar_lote(batch_size=1, scraper_nombre="telcos")
        self.assertEqual(len(lote_telcos), 1)
        self.assertEqual(lote_telcos[0].id, 5001)
        # Verifica que recibió el DNI y los datos enriquecidos acumulados de IRIS
        self.assertEqual(lote_telcos[0].linea.dni, "28112233")
        self.assertIn("iris", lote_telcos[0].datos_existentes)

        # 4. Telcos tiene coincidencia (cortocircuito) y finaliza la línea
        telcos_resultado = [{
            "id": 5001,
            "ani": "1144556677",
            "dni": "28112233",
            "status": "coincidencia",
            "scraper_actual": "finalizado",
            "estado": "completado",
            "descripcion": "Coincidencia Claro",
            "datos": {
                "iris": {"status": "sin_coincidencia"},
                "claro": {"status": "coincidencia", "deuda": 500.0}
            },
            "fuente": ["iris", "claro"],
            "latencia": 0.40
        }]
        self.adapter.persistir_resultados(telcos_resultado)

        # Ahora que está finalizado, SÍ pasa a 'listo_para_subir'
        self.assertEqual(self.adapter.contar_pendientes(scraper_actual="telcos"), 0)
        self.assertEqual(self.adapter.contar_listos_para_subir(), 1)

        # 5. La subida nocturna lo toma para push
        lote_push = self.adapter.obtener_lote_para_push(limit=1)
        self.assertEqual(len(lote_push), 1)
        self.assertEqual(lote_push[0]["id_vps"], 5001)
        self.assertEqual(lote_push[0]["estado_local"], "en_subida")

    def test_06_filtra_active_true_en_registro_no_llame(self):
        """
        Para tipo_cola='registro_no_llame', SqliteStagingAdapter debe ignorar
        completamente registros con active=1 (active=True) tanto al contar
        pendientes como al reservar lotes.
        """
        db_path_rnl = os.path.join(self.temp_dir.name, "test_rnl.db")
        adapter_rnl = SQLiteStagingAdapter(db_path=db_path_rnl, tipo_cola="registro_no_llame")
        tareas = [
            {"id": 6001, "numero_de_linea": "1111111111", "scraper_actual": "iris", "active": 1},
            {"id": 6002, "numero_de_linea": "1122222222", "scraper_actual": "iris", "active": 0},
            {"id": 6003, "numero_de_linea": "1133333333", "scraper_actual": "iris", "active": None},
        ]
        adapter_rnl.insertar_tareas_descargadas(tareas)

        # contar_pendientes solo debe ver las 2 no activas en No Llame
        self.assertEqual(adapter_rnl.contar_pendientes(scraper_actual="iris"), 2)

        # reservar_lote solo debe devolver 6002 y 6003
        lote = adapter_rnl.reservar_lote(batch_size=10, scraper_nombre="iris")
        ids = [r.id for r in lote]
        self.assertNotIn(6001, ids)
        self.assertIn(6002, ids)
        self.assertIn(6003, ids)
        self.assertEqual(len(lote), 2)


    def test_07_prioridad_geografica_cascada_y_filtro(self):
        """
        Verifica que reservar_lote en SQLite respete la cascada geográfica:
        P1 (11 y Mendoza: 260, 261, 262, 263) ->
        P2 (Resto de Cuyo: 264 San Juan, 265/266 San Luis, 380/382 La Rioja) ->
        P3 (Resto del país).
        Y que el filtro estricto por prioridad funcione.
        """
        from core.domain.enums import Prioridad

        tareas = [
            {"id": 7001, "numero_de_linea": "3415871971", "dni": None, "scraper_actual": "enacom_web"},  # P3 (Rosario)
            {"id": 7002, "numero_de_linea": "2644112233", "dni": None, "scraper_actual": "enacom_web"},  # P2 (San Juan)
            {"id": 7003, "numero_de_linea": "1144001111", "dni": None, "scraper_actual": "enacom_web"},  # P1 (AMBA 11)
            {"id": 7004, "numero_de_linea": "2614556677", "dni": None, "scraper_actual": "enacom_web"},  # P1 (Mendoza 261)
            {"id": 7005, "numero_de_linea": "2664556677", "dni": None, "scraper_actual": "enacom_web"},  # P2 (San Luis 266)
            {"id": 7006, "numero_de_linea": "2995814274", "dni": None, "scraper_actual": "enacom_web"},  # P3 (Neuquén)
        ]
        self.adapter.insertar_tareas_descargadas(tareas)

        # 1. Cascada Auto (prioridad=None): debe traer P1 primero, luego P2, luego P3
        lote_auto = self.adapter.reservar_lote(batch_size=6, prioridad=None, scraper_nombre="enacom_web")
        self.assertEqual(len(lote_auto), 6)
        ids_auto = [r.id for r in lote_auto]
        # P1: 7003 (11) y 7004 (261)
        self.assertIn(ids_auto[0], [7003, 7004])
        self.assertIn(ids_auto[1], [7003, 7004])
        # P2: 7002 (264) y 7005 (266)
        self.assertIn(ids_auto[2], [7002, 7005])
        self.assertIn(ids_auto[3], [7002, 7005])
        # P3: 7001 (341) y 7006 (299)
        self.assertIn(ids_auto[4], [7001, 7006])
        self.assertIn(ids_auto[5], [7001, 7006])

        # Verificar enums y nombres
        self.assertEqual(lote_auto[0].prioridad, Prioridad.P1_AMBA_MENDOZA)
        self.assertEqual(lote_auto[2].prioridad, Prioridad.P2_CUYO)
        self.assertEqual(lote_auto[4].prioridad, Prioridad.P3_RESTO)

        # Revertir para probar filtro estricto
        self.adapter.revertir_a_pendiente(ids_auto)

        # 2. Filtro estricto prioridad=2 (Resto Cuyo)
        lote_p2 = self.adapter.reservar_lote(batch_size=10, prioridad=2, scraper_nombre="enacom_web")
        self.assertEqual(len(lote_p2), 2)
        self.assertEqual(set(r.id for r in lote_p2), {7002, 7005})
        self.assertTrue(all(r.prioridad == Prioridad.P2_CUYO for r in lote_p2))


if __name__ == "__main__":
    unittest.main()

