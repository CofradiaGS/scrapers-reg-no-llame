# -*- coding: utf-8 -*-
import os
import time
import tempfile
import unittest
from adapters.network.cooldown_manager import IPCooldownManager


class TestIPCooldownManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_cooldown.db")
        self.mgr = IPCooldownManager(db_path=self.db_path, default_cooldown_sec=1.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_strike_uno_no_activa_descanso(self):
        strikes, descansando = self.mgr.registrar_bloqueo("1.2.3.4")
        self.assertEqual(strikes, 1)
        self.assertFalse(descansando)
        self.assertFalse(self.mgr.esta_en_descanso("1.2.3.4"))

    def test_02_strike_dos_activa_descanso(self):
        self.mgr.registrar_bloqueo("1.2.3.4")
        strikes, descansando = self.mgr.registrar_bloqueo("1.2.3.4")
        self.assertEqual(strikes, 2)
        self.assertTrue(descansando)
        self.assertTrue(self.mgr.esta_en_descanso("1.2.3.4"))
        self.assertIn("1.2.3.4", self.mgr.obtener_ips_en_descanso())

    def test_03_expiracion_del_descanso(self):
        # Cooldown corto de 0.2 segundos
        self.mgr.registrar_bloqueo("5.6.7.8", cooldown_segundos=0.2)
        _, descansando = self.mgr.registrar_bloqueo("5.6.7.8", cooldown_segundos=0.2)
        self.assertTrue(descansando)
        self.assertTrue(self.mgr.esta_en_descanso("5.6.7.8"))

        # Esperar a que expire
        time.sleep(0.3)
        self.assertFalse(self.mgr.esta_en_descanso("5.6.7.8"))
        self.assertNotIn("5.6.7.8", self.mgr.obtener_ips_en_descanso())

    def test_04_reportar_exito_resetea_strikes(self):
        self.mgr.registrar_bloqueo("9.9.9.9")
        self.mgr.reportar_exito("9.9.9.9")
        # El siguiente bloqueo vuelve a ser strike 1
        strikes, descansando = self.mgr.registrar_bloqueo("9.9.9.9")
        self.assertEqual(strikes, 1)
        self.assertFalse(descansando)


if __name__ == "__main__":
    unittest.main()
