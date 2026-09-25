# -*- coding: utf-8 -*-
"""
Pruebas Unitarias: Centinela Explorador (Scout Probe) y Pausa Coordinada
Verifica:
1. Escalera de backoff progresivo (60s -> 180s -> 300s -> 600s -> máx 900s/15m).
2. Pausa coordinada cuando la cola está vacía.
3. Despachador IPC: cortocircuito de reservar_lote durante pausa por cola_vacia sin consultar BD.
4. Handover de pre-fetch del Centinela a los workers sin consulta duplicada.
5. Acción 'scout_probe' en el despachador IPC.
"""
import unittest
from unittest.mock import MagicMock
from queue import Queue
import threading
import time

from runtime.supervisor import SupervisorIndustrial
from core.domain.entities import RegistroCola


class TestSupervisorCentinela(unittest.TestCase):

    def setUp(self):
        self.supervisor = SupervisorIndustrial(
            scraper_name="personal",
            workers=5,
            batch_size=20,
            empty_queue_pause_max_sec=900.0
        )
        self.mock_adapter = MagicMock()
        self.supervisor.db_adapter = self.mock_adapter

    def test_01_escalera_backoff_progresivo(self):
        """Verifica la progresión del retardo de sondeo en la escalera."""
        self.supervisor._empty_backoff_level = 0
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 60.0)

        self.supervisor._empty_backoff_level = 1
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 180.0)

        self.supervisor._empty_backoff_level = 2
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 300.0)

        self.supervisor._empty_backoff_level = 3
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 600.0)

        self.supervisor._empty_backoff_level = 4
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 900.0)

        self.supervisor._empty_backoff_level = 10
        self.assertEqual(self.supervisor._get_current_empty_backoff_delay(), 900.0)

    def test_02_escalera_con_techo_personalizado(self):
        """Verifica que un techo personalizado menor a 900s tope los niveles superiores."""
        sup_custom = SupervisorIndustrial(
            scraper_name="claro",
            workers=3,
            empty_queue_pause_max_sec=120.0
        )
        sup_custom._empty_backoff_level = 0
        self.assertEqual(sup_custom._get_current_empty_backoff_delay(), 60.0)

        sup_custom._empty_backoff_level = 1
        self.assertEqual(sup_custom._get_current_empty_backoff_delay(), 120.0)

        sup_custom._empty_backoff_level = 5
        self.assertEqual(sup_custom._get_current_empty_backoff_delay(), 120.0)

    def test_03_estado_pausa_cola_vacia(self):
        """Verifica que _obtener_estado_pausa describa el estado del centinela y nivel."""
        self.supervisor._set_pause("cola_vacia")
        self.assertTrue(self.supervisor.pause_event.is_set())

        estado = self.supervisor._obtener_estado_pausa()
        self.assertIn("PAUSADO", estado)
        self.assertIn("Cola Vacía", estado)
        self.assertIn("Centinela esperando 60s", estado)
        self.assertIn("Nivel 1", estado)

        self.supervisor._clear_pause("cola_vacia")
        self.assertFalse(self.supervisor.pause_event.is_set())

    def test_04_dispatcher_short_circuit_cuando_cola_vacia(self):
        """
        Verifica que el despachador IPC NO consulte a la BD si 'cola_vacia' está activa,
        respondiendo [] inmediatamente al worker.
        """
        self.supervisor._set_pause("cola_vacia")
        resp_q = Queue()
        self.supervisor.slot_response_queues[1] = resp_q

        # Iniciar despachador en un hilo
        disp_thread = threading.Thread(target=self.supervisor._db_dispatcher_loop, daemon=True)
        disp_thread.start()

        # Enviar solicitud de reserva desde el worker slot 1
        self.supervisor.db_request_queue.put({
            "action": "reservar_lote",
            "slot_id": 1,
            "batch_size": 20
        })

        lote_recibido = resp_q.get(timeout=2.0)
        self.assertEqual(lote_recibido, [])
        # Asegurar que el adaptador de BD NO fue llamado
        self.mock_adapter.reservar_lote.assert_not_called()

        self.supervisor._dispatcher_stop.set()
        disp_thread.join(timeout=2.0)

    def test_05_dispatcher_handover_prefetched_lote(self):
        """
        Verifica que si el centinela dejó un lote pre-reservado, el primer worker
        lo reciba sin realizar una consulta duplicada a MySQL.
        """
        dummy_registro = {"id": 1001, "ani": "2614567890", "scraper_actual": "personal", "estado": "pendiente"}
        self.supervisor._scout_prefetched_lote = [dummy_registro]

        resp_q = Queue()
        self.supervisor.slot_response_queues[1] = resp_q

        disp_thread = threading.Thread(target=self.supervisor._db_dispatcher_loop, daemon=True)
        disp_thread.start()

        self.supervisor.db_request_queue.put({
            "action": "reservar_lote",
            "slot_id": 1,
            "batch_size": 20
        })

        lote_recibido = resp_q.get(timeout=2.0)
        self.assertEqual(lote_recibido, [dummy_registro])
        self.assertEqual(self.supervisor._scout_prefetched_lote, [])
        self.mock_adapter.reservar_lote.assert_not_called()

        self.supervisor._dispatcher_stop.set()
        disp_thread.join(timeout=2.0)

    def test_06_dispatcher_scout_probe_exitoso(self):
        """
        Verifica que una acción 'scout_probe' exitosa guarde el lote en memoria,
        desactive 'cola_vacia' y despierte a los workers.
        """
        self.supervisor._set_pause("cola_vacia")
        self.supervisor._empty_backoff_level = 3
        dummy_registro = {"id": 2002, "ani": "1144556677", "scraper_actual": "personal", "estado": "pendiente"}
        self.mock_adapter.reservar_lote.return_value = [dummy_registro]

        resp_q = Queue()
        self.supervisor.slot_response_queues[-1] = resp_q

        disp_thread = threading.Thread(target=self.supervisor._db_dispatcher_loop, daemon=True)
        disp_thread.start()

        self.supervisor.db_request_queue.put({
            "action": "scout_probe",
            "slot_id": -1,
            "batch_size": 20,
            "prioridad": None,
            "scraper_nombre": "personal",
            "solo_sin_coincidencia": False
        })

        res = resp_q.get(timeout=2.0)
        self.assertTrue(res.get("encontrados"))
        self.assertEqual(res.get("cantidad"), 1)
        self.assertEqual(self.supervisor._scout_prefetched_lote, [dummy_registro])
        self.assertEqual(self.supervisor._empty_backoff_level, 0)
        self.assertNotIn("cola_vacia", self.supervisor._pause_reasons)
        self.assertFalse(self.supervisor.pause_event.is_set())

        self.supervisor._dispatcher_stop.set()
        disp_thread.join(timeout=2.0)

    def test_07_circuit_breaker_suspende_pings_fuera_de_horario(self):
        """
        Verifica que el CircuitBreaker NO emita peticiones HTTP si 'horario'
        está en las causas de pausa activas.
        """
        import urllib.request
        from unittest.mock import patch

        self.supervisor._set_pause("horario")
        self.supervisor.verificar_vpn = True
        self.supervisor.scraper_name = "iris_http"

        with patch("urllib.request.urlopen") as mock_urlopen:
            cb_thread = threading.Thread(target=self.supervisor._circuit_breaker_loop, kwargs={"check_interval": 1.0}, daemon=True)
            cb_thread.start()

            # Dejar correr brevemente
            time.sleep(0.5)
            self.supervisor.stop_event.set()
            cb_thread.join(timeout=2.0)

            # urlopen no debió ser llamado nunca porque estamos fuera de horario
            mock_urlopen.assert_not_called()

    def test_08_horario_comercial_calculo_determinista(self):
        """
        Verifica que la política de horario comercial calcule segundos y próxima apertura
        sin bucles de sondeo ciego.
        """
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=-3))

        # Simular un domingo a las 14:00 (fuera de horario comercial)
        domingo = datetime(2026, 9, 27, 14, 0, 0, tzinfo=tz)
        self.assertFalse(self.supervisor.politica_horario.esta_en_horario(domingo))

        # La próxima apertura debe ser el lunes 28/09 a las 08:00
        prox = self.supervisor.politica_horario.proxima_apertura(domingo)
        self.assertEqual(prox.weekday(), 0)  # Lunes
        self.assertEqual(prox.hour, 8)
        self.assertEqual(prox.minute, 0)

        # Segundos exactos de reposo calculados en 1 sola llamada
        segundos = self.supervisor.politica_horario.segundos_hasta_proxima_apertura(domingo)
        # De domingo 14:00 a lunes 08:00 hay 18 horas = 64,800 segundos
        self.assertEqual(segundos, 18 * 3600)


if __name__ == "__main__":
    unittest.main()
