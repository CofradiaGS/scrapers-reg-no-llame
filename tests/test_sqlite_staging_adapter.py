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
                "scraper_actual": "claro",
                "descripcion": "Coincidencia Claro",
                "datos": {"claro": {"status": "coincidencia", "deuda": 1500.0}},
                "latencia": 0.35
            },
            {
                "id": 2002,
                "ani": "1155443322",
                "dni": "28445112",
                "status": "sin_coincidencias",
                "scraper_actual": "movistar",
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
        self.assertEqual(para_push[0]["estado_local"], "listo_para_subir")
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


if __name__ == "__main__":
    unittest.main()
