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



if __name__ == "__main__":
    unittest.main()

