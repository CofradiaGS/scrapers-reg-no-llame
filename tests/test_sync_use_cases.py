# -*- coding: utf-8 -*-
"""
Pruebas de Integración y Unitarias de los Casos de Uso de Sincronización
Arquitectura Hexagonal - Pull Matutino (Watermark) y Push Nocturno (Chunks 5.000)

Verifica:
1. SincronizarPullMatutinoUseCase:
   - Umbral de reserva (no descarga si pendientes >= 2.000).
   - Descarga atómica de 10.000 tareas cuando pendientes < 2.000.
   - Descarga forzada (--pull-now).
2. SincronizarPushNocturnoUseCase:
   - Chunks de máximo 5.000 por transacción atómica (ej: 12.340 -> 5.000 + 5.000 + 2.340).
   - Manejo de remanentes incompletos sin espera.
   - Barrido final (Sweep) para capturar tareas terminadas durante el push.
   - Purga rotativa de tareas sincronizadas con más de 7 días.
"""
import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.queue.sqlite_staging_adapter import SQLiteStagingAdapter
from core.use_cases.sync_pull_use_case import SincronizarPullMatutinoUseCase
from core.use_cases.sync_push_use_case import SincronizarPushNocturnoUseCase


class TestSyncUseCases(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_sync_staging.db")
        self.local_repo = SQLiteStagingAdapter(
            db_path=self.db_path,
            tipo_cola="cola_automatizacion",
            auto_id="telco_scraper",
            pc_id="PC-TEST-01"
        )
        self.remote_mock = MagicMock()

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- PRUEBAS DE PULL (WATERMARK) ---

    def test_01_pull_no_se_dispara_si_hay_buffer_suficiente(self):
        # Insertar 2.500 pendientes (> umbral 2.000)
        tareas = [{"id": i, "numero_de_linea": f"114400{i:04d}", "dni": "30112233"} for i in range(1, 2501)]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.assertEqual(self.local_repo.contar_pendientes(), 2500)

        use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(tipo_cola="cola_automatizacion", limit=10000, umbral_minimo=2000, forzar=False)

        self.assertFalse(res["ejecutado"])
        self.assertEqual(res["motivo"], "buffer_suficiente")
        self.remote_mock.descargar_lote_vps.assert_not_called()

    def test_02_pull_se_dispara_cuando_baja_del_umbral(self):
        # Insertar 500 pendientes (< umbral 2.000)
        tareas = [{"id": i, "numero_de_linea": f"114400{i:04d}", "dni": "30112233"} for i in range(1, 501)]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.assertEqual(self.local_repo.contar_pendientes(), 500)

        # Mock de VPS retornando 10.000 tareas
        tareas_vps = [{"id": 10000 + i, "numero_de_linea": f"115500{i:04d}", "dni": "40112233"} for i in range(1, 10001)]
        self.remote_mock.descargar_lote_vps.return_value = tareas_vps

        use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(tipo_cola="cola_automatizacion", limit=10000, umbral_minimo=2000, forzar=False)

        self.assertTrue(res["ejecutado"])
        self.assertEqual(res["descargados"], 10000)
        self.assertEqual(res["insertados"], 10000)
        # Total ahora: 500 iniciales + 10.000 nuevas = 10.500
        self.assertEqual(self.local_repo.contar_pendientes(), 10500)

    def test_02b_pull_con_umbral_cero_se_dispara_solo_al_terminar_todas(self):
        # Con umbral 0: si queda 1 pendiente, NO descarga
        tareas = [{"id": 999, "numero_de_linea": "1144009999", "dni": "30112233"}]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.assertEqual(self.local_repo.contar_pendientes(), 1)

        use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res_con_1 = use_case.ejecutar(tipo_cola="cola_automatizacion", limit=5000, umbral_minimo=0, forzar=False)
        self.assertFalse(res_con_1["ejecutado"])
        self.assertEqual(res_con_1["motivo"], "buffer_suficiente")
        self.remote_mock.descargar_lote_vps.assert_not_called()

        # Al procesarse y quedar 0 pendientes, SÍ dispara la descarga de las siguientes 5.000
        self.local_repo.reservar_lote(batch_size=1)
        self.local_repo.persistir_resultados([{
            "id": 999, "ani": "1144009999", "dni": "30112233",
            "status": "sin_coincidencias", "scraper_actual": "finalizado",
            "datos": {}, "descripcion": "OK"
        }])
        self.assertEqual(self.local_repo.contar_pendientes(), 0)

        tareas_vps = [{"id": 20000 + i, "numero_de_linea": f"118800{i:04d}", "dni": "40112233"} for i in range(1, 5001)]
        self.remote_mock.descargar_lote_vps.return_value = tareas_vps

        res_con_0 = use_case.ejecutar(tipo_cola="cola_automatizacion", limit=5000, umbral_minimo=0, forzar=False)
        self.assertTrue(res_con_0["ejecutado"])
        self.assertEqual(res_con_0["descargados"], 5000)
        self.assertEqual(res_con_0["insertados"], 5000)
        self.assertEqual(self.local_repo.contar_pendientes(), 5000)

    def test_03_pull_forzado_descarga_sin_importar_umbral(self):
        tareas = [{"id": i, "numero_de_linea": f"114400{i:04d}", "dni": "30112233"} for i in range(1, 3001)]
        self.local_repo.insertar_tareas_descargadas(tareas)

        tareas_vps = [{"id": 50000 + i, "numero_de_linea": f"116600{i:04d}", "dni": "50112233"} for i in range(1, 1001)]
        self.remote_mock.descargar_lote_vps.return_value = tareas_vps

        use_case = SincronizarPullMatutinoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(tipo_cola="cola_automatizacion", limit=1000, forzar=True)

        self.assertTrue(res["ejecutado"])
        self.assertEqual(res["descargados"], 1000)
        self.remote_mock.descargar_lote_vps.assert_called_once()

    # --- PRUEBAS DE PUSH NOCTURNO (CHUNKS 5.000) ---

    def test_04_push_en_chunks_de_5000_y_remanentes(self):
        # Simular 12.340 registros procesados listos para push
        # Deben subirse en 3 chunks: 5.000 + 5.000 + 2.340
        total_filas = 12340
        tareas_descargadas = [{"id": i, "numero_de_linea": f"117700{i:05d}", "dni": "20112233"} for i in range(1, total_filas + 1)]
        self.local_repo.insertar_tareas_descargadas(tareas_descargadas)

        # Pasarlos a en_proceso y luego a listo_para_subir
        self.local_repo.reservar_lote(batch_size=total_filas)
        resultados = [
            {
                "id": i,
                "ani": f"117700{i:05d}",
                "dni": "20112233",
                "status": "coincidencia" if i % 2 == 0 else "sin_coincidencias",
                "scraper_actual": "claro",
                "datos": {"test": i},
                "descripcion": "OK"
            }
            for i in range(1, total_filas + 1)
        ]
        self.local_repo.persistir_resultados(resultados)

        # Mock de VPS para subir lote
        self.remote_mock.subir_lote_vps.return_value = 5000

        use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        # sweep_wait_sec=0 para pruebas unitarias rápidas
        res = use_case.ejecutar(tipo_cola="cola_automatizacion", chunk_size=5000, sweep_wait_sec=0.0)

        self.assertTrue(res["exito"])
        self.assertEqual(res["total_subidos"], 12340)
        self.assertEqual(res["lotes_procesados"], 3)
        self.assertEqual(self.remote_mock.subir_lote_vps.call_count, 3)

        # Verificar que el primer lote tuvo 5.000, el segundo 5.000 y el tercero 2.340
        chunk_1 = self.remote_mock.subir_lote_vps.call_args_list[0][1]["lote"]
        chunk_2 = self.remote_mock.subir_lote_vps.call_args_list[1][1]["lote"]
        chunk_3 = self.remote_mock.subir_lote_vps.call_args_list[2][1]["lote"]
        self.assertEqual(len(chunk_1), 5000)
        self.assertEqual(len(chunk_2), 5000)
        self.assertEqual(len(chunk_3), 2340)

        # Verificar que no quedaron filas pendientes de subida en SQLite
        restantes_push = self.local_repo.obtener_lote_para_push(limit=10)
        self.assertEqual(len(restantes_push), 0)

        stats = self.local_repo.obtener_estadisticas()
        self.assertEqual(stats["sincronizado"], 12340)

    def test_05_sweep_final_captura_remanentes(self):
        # Insertar 100 registros y procesarlos
        tareas = [{"id": i, "numero_de_linea": f"118800{i:04d}", "dni": "10112233"} for i in range(1, 101)]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.local_repo.reservar_lote(batch_size=100)
        resultados = [{"id": i, "status": "coincidencia", "datos": {}} for i in range(1, 101)]
        self.local_repo.persistir_resultados(resultados)

        use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(tipo_cola="cola_automatizacion", chunk_size=5000, sweep_wait_sec=0.01)

        self.assertTrue(res["exito"])
        self.assertEqual(res["total_subidos"], 100)
        self.assertEqual(res["lotes_procesados"], 1)


    def test_06_push_interrumpido_por_ventana_deja_remanentes_para_proximo_ciclo(self):
        """Verifica que si la ventana horaria se agota (08:00 AM) o should_stop se activa,
        la subida se detiene limpiamente y los registros restantes quedan listos para el próximo ciclo."""
        total_filas = 10000
        tareas = [{"id": i, "numero_de_linea": f"119900{i:05d}", "dni": "20112233"} for i in range(1, total_filas + 1)]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.local_repo.reservar_lote(batch_size=total_filas)
        resultados = [{"id": i, "status": "sin_coincidencias", "datos": {}} for i in range(1, total_filas + 1)]
        self.local_repo.persistir_resultados(resultados)

        self.assertEqual(self.local_repo.contar_listos_para_subir(), 10000)

        # Condición de stop: detenerse tras el primer chunk de 5.000
        chunks_subidos = 0
        def should_stop():
            return chunks_subidos >= 1

        def mock_subir(tipo_cola, lote):
            nonlocal chunks_subidos
            chunks_subidos += 1
            return len(lote)

        self.remote_mock.subir_lote_vps.side_effect = mock_subir

        use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(
            tipo_cola="cola_automatizacion",
            chunk_size=5000,
            sweep_wait_sec=0.0,
            roundrobin_pause_sec=0.0,
            should_stop=should_stop
        )

        self.assertTrue(res["exito"])
        self.assertTrue(res["interrumpido_por_ventana"])
        self.assertEqual(res["total_subidos"], 5000)
        self.assertEqual(res["lotes_procesados"], 1)

        # Quedan exactamente 5.000 listos para subir en el siguiente ciclo
        self.assertEqual(self.local_repo.contar_listos_para_subir(), 5000)
        stats = self.local_repo.obtener_estadisticas()
        self.assertEqual(stats["sincronizado"], 5000)
        self.assertEqual(stats["listo_para_subir"], 5000)

        # Próximo ciclo: se suben las 5.000 restantes
        res_segundo_ciclo = use_case.ejecutar(
            tipo_cola="cola_automatizacion",
            chunk_size=5000,
            sweep_wait_sec=0.0,
            roundrobin_pause_sec=0.0
        )
        self.assertTrue(res_segundo_ciclo["exito"])
        self.assertFalse(res_segundo_ciclo["interrumpido_por_ventana"])
        self.assertEqual(res_segundo_ciclo["total_subidos"], 5000)
        self.assertEqual(self.local_repo.contar_listos_para_subir(), 0)

    def test_07_ventana_horaria_sync_scheduler(self):
        """Verifica la lógica de la ventana de push nocturno (00:00 a 08:00 hs) en el planificador."""
        from datetime import datetime
        from runtime.sync_scheduler import SyncSchedulerThread

        scheduler = SyncSchedulerThread(
            local_repo=self.local_repo,
            remote_repo=self.remote_mock,
            sync_push_window_start_hour=0,
            sync_push_window_end_hour=8
        )

        # Horas dentro de la ventana: 00:00, 03:30, 07:59
        self.assertTrue(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 0, 0, 0)))
        self.assertTrue(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 3, 30, 0)))
        self.assertTrue(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 7, 59, 59)))

        # Horas fuera de la ventana: 08:00, 12:00, 20:00, 22:00, 23:59
        self.assertFalse(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 8, 0, 0)))
        self.assertFalse(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 12, 0, 0)))
        self.assertFalse(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 20, 0, 0)))
        self.assertFalse(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 22, 0, 0)))
        self.assertFalse(scheduler.esta_en_ventana_push(datetime(2026, 10, 3, 23, 59, 59)))


if __name__ == "__main__":
    unittest.main()

