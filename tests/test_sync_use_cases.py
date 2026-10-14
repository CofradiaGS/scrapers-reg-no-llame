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
import config



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
                "scraper_actual": "finalizado",
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

    def test_08_vps_sync_adapter_descargar_telcos(self):
        """Verifica que VPSSyncAdapter descargue y normalice correctamente tareas para 'telcos' sin sobreescritura vacía."""
        from unittest.mock import patch, MagicMock
        from adapters.queue.vps_sync_adapter import VPSSyncAdapter

        adapter = VPSSyncAdapter(pool_size=1)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Simular lote con DNI retornado por query_con_dni
        filas_con_dni = [
            {
                "id": 101,
                "ani": "1122334455",
                "dni": "30112233",
                "estado": "pendiente",
                "scraper_actual": "movistar",
                "fuente": "[\"iris\", \"claro\", \"personal\"]",
                "datos_json": "{\"iris\": {\"status\": \"no_coincidencia\"}}"
            }
        ]
        # fetchall retorna las filas en la primera llamada y vacío en la segunda (si se hiciera sin dni)
        mock_cursor.fetchall.side_effect = [filas_con_dni, []]

        with patch.object(adapter, "_get_connection", return_value=mock_conn):
            tareas = adapter.descargar_lote_vps(
                tipo_cola="registro_no_llame",
                limit=10,
                scraper_actual="telcos"
            )

        self.assertEqual(len(tareas), 1)
        self.assertEqual(tareas[0]["id"], 101)
        self.assertEqual(tareas[0]["numero_de_linea"], "1122334455")
        self.assertEqual(tareas[0]["dni"], "30112233")
        self.assertEqual(tareas[0]["scraper_actual"], "telcos")
        mock_conn.commit.assert_called_once()

    def test_09_push_exigir_lote_completo_no_sube_remanentes_menores_a_5000(self):
        """
        Regla de negocio: si exigir_lote_completo=True, se suben lotes consecutivos
        de a 5.000 mientras haya >= 5.000, y se detiene cuando el remanente sea < 5.000.
        Para 12.340 registros: debe subir 2 lotes de 5.000 (10.000) y dejar 2.340 en SQLite.
        """
        total_filas = 12340
        tareas = [{"id": i, "numero_de_linea": f"113300{i:05d}", "dni": "20112233"} for i in range(1, total_filas + 1)]
        self.local_repo.insertar_tareas_descargadas(tareas)
        self.local_repo.reservar_lote(batch_size=total_filas)
        resultados = [{"id": i, "status": "coincidencia", "datos": {}} for i in range(1, total_filas + 1)]
        self.local_repo.persistir_resultados(resultados)

        self.assertEqual(self.local_repo.contar_listos_para_subir(), 12340)

        use_case = SincronizarPushNocturnoUseCase(remote_repo=self.remote_mock, local_repo=self.local_repo)
        res = use_case.ejecutar(
            tipo_cola="cola_automatizacion",
            chunk_size=5000,
            sweep_wait_sec=0.0,
            roundrobin_pause_sec=0.0,
            exigir_lote_completo=True
        )

        self.assertTrue(res["exito"])
        # Deben subirse exactamente 10.000 (2 chunks de 5.000)
        self.assertEqual(res["total_subidos"], 10000)
        self.assertEqual(res["lotes_procesados"], 2)
        # Remanente menor a 5.000 (2.340) permanece en SQLite local para no parar la producción
        self.assertEqual(self.local_repo.contar_listos_para_subir(), 2340)

    def test_10_politica_ventana_push_dominio_argentina(self):
        """Verifica la regla de dominio PoliticaVentanaPush (00:00 a 21:00 hs habilitado; 21:00 a 00:00 hs veda)."""
        from datetime import datetime
        from core.domain.schedule import PoliticaVentanaPush

        politica = PoliticaVentanaPush(
            hora_veda_inicio=21,
            hora_veda_fin=0,
            chunk_size=5000
        )

        # Ventana habilitada: 00:00, 10:30, 20:59:59
        self.assertTrue(politica.esta_en_ventana(datetime(2026, 10, 14, 0, 0, 0)))
        self.assertTrue(politica.esta_en_ventana(datetime(2026, 10, 14, 10, 30, 0)))
        self.assertTrue(politica.esta_en_ventana(datetime(2026, 10, 14, 20, 59, 59)))

        # Veda nocturna (prohibido): 21:00, 22:15, 23:59:59
        self.assertFalse(politica.esta_en_ventana(datetime(2026, 10, 14, 21, 0, 0)))
        self.assertFalse(politica.esta_en_ventana(datetime(2026, 10, 14, 22, 15, 0)))
        self.assertFalse(politica.esta_en_ventana(datetime(2026, 10, 14, 23, 59, 59)))

        # Condición de disparo: >= 5.000 registros y en ventana
        dt_habilitado = datetime(2026, 10, 14, 15, 0, 0)
        dt_veda = datetime(2026, 10, 14, 21, 30, 0)

        self.assertFalse(politica.debe_subir(listos_para_subir=4999, dt=dt_habilitado))
        self.assertTrue(politica.debe_subir(listos_para_subir=5000, dt=dt_habilitado))
        self.assertTrue(politica.debe_subir(listos_para_subir=155000, dt=dt_habilitado))
        # En veda nunca sube salvo forzado
        self.assertFalse(politica.debe_subir(listos_para_subir=155000, dt=dt_veda, forzar=False))
        self.assertTrue(politica.debe_subir(listos_para_subir=155000, dt=dt_veda, forzar=True))

        # Cálculo de lotes continuos: 155.000 / 5.000 = 31 lotes exactos
        self.assertEqual(politica.calcular_lotes_a_subir(155000), 31)
        self.assertEqual(politica.calcular_lotes_a_subir(12340), 2)
        self.assertEqual(politica.calcular_lotes_a_subir(12340, forzar=True), 3)

    def test_11_sync_scheduler_push_desacoplado_no_bloquea_pull(self):
        """Verifica que pull y push tienen locks independientes y pueden concurrir sin bloquearse mutuamente."""
        from runtime.sync_scheduler import SyncSchedulerThread

        scheduler = SyncSchedulerThread(
            local_repo=self.local_repo,
            remote_repo=self.remote_mock,
            sync_push_window_start_hour=0,
            sync_push_window_end_hour=21
        )

        # Simular que el lock de push está adquirido (subida en curso)
        with scheduler._push_lock:
            # El lock de pull DEBE estar libre y disponible
            adquirido_pull = scheduler._pull_lock.acquire(blocking=False)
            self.assertTrue(adquirido_pull, "El lock de pull debe ser independiente y no quedar bloqueado por el push")
            scheduler._pull_lock.release()

    def test_12_boot_sweeper_reconciliacion_remota_vps(self):
        """Verifica que SyncSchedulerThread ejecuta la auto-sanación de huérfanos al arrancar."""
        import threading
        from runtime.sync_scheduler import SyncSchedulerThread

        self.remote_mock.liberar_huerfanos_vps.return_value = 147911
        self.remote_mock.descargar_lote_vps.return_value = []

        stop_evt = threading.Event()
        stop_evt.set()  # Detener de inmediato tras la fase de boot

        scheduler = SyncSchedulerThread(
            local_repo=self.local_repo,
            remote_repo=self.remote_mock,
            stop_event=stop_evt,
            tipo_cola="cola_automatizacion",
            auto_id="scraper_cobro_express",
            pc_id="PC-01"
        )
        scheduler.run()

        # Comprobar que se invocó liberar_huerfanos_vps en el boot con el umbral de 12h (720 min)
        timeout_esperado = getattr(config, "obtener_timeout_huerfanos_vps", lambda s: 720)("telcos")
        self.remote_mock.liberar_huerfanos_vps.assert_called_with(
            tipo_cola="cola_automatizacion",
            minutos_inactividad=timeout_esperado,
            auto_id="scraper_cobro_express",
            pc_id="PC-01",
            scraper_actual="telcos"
        )

    def test_timeout_huerfanos_12_horas_configuracion(self):
        """Verifica que el umbral de huérfanos por defecto sea 720 minutos (12 horas)."""
        self.assertEqual(config.VPS_ORPHAN_TIMEOUT_MINUTES, 720)
        self.assertEqual(config.VPS_ORPHAN_TIMEOUT_HOURS, 12.0)
        self.assertEqual(config.obtener_timeout_huerfanos_vps("telcos"), 720)
        self.assertEqual(config.obtener_timeout_huerfanos_vps("iris_browser"), 720)
        self.assertEqual(config.obtener_timeout_huerfanos_vps("desconocido"), 720)


if __name__ == "__main__":
    unittest.main()





