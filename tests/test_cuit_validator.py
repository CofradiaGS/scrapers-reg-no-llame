# -*- coding: utf-8 -*-
"""
Pruebas Unitarias del Validador y Generador Algorítmico de CUIT/CUIL (cuit_validator.py).
Verifica:
1. Módulo 11 con CUITs válidos e inválidos.
2. Inferencia de género por nombre onomástico argentino.
3. Generación de candidatos primario y secundario ante colisiones.
"""
import unittest
from core.domain.cuit_validator import (
    validar_cuit_modulo11,
    calcular_cuil,
    inferir_genero,
    obtener_cuils_candidatos
)


class TestCuitValidator(unittest.TestCase):

    def test_validar_cuit_modulo11(self):
        # CUITs válidos conocidos
        self.assertTrue(validar_cuit_modulo11("20301122331"))
        self.assertTrue(validar_cuit_modulo11("27351234569"))
        self.assertTrue(validar_cuit_modulo11("30500010912"))  # Banco Central

        # CUITs inválidos
        self.assertFalse(validar_cuit_modulo11("20301122330"))  # DV erróneo
        self.assertFalse(validar_cuit_modulo11("12345"))        # Longitud incorrecta
        self.assertFalse(validar_cuit_modulo11(""))

    def test_calcular_cuil(self):
        # Varón
        c_m = calcular_cuil("30112233", "M")
        self.assertTrue(validar_cuit_modulo11(c_m))
        self.assertTrue(c_m.startswith(("20", "23")))

        # Mujer
        c_f = calcular_cuil("35123456", "F")
        self.assertTrue(validar_cuit_modulo11(c_f))
        self.assertTrue(c_f.startswith(("27", "23")))

    def test_inferir_genero(self):
        self.assertEqual(inferir_genero("JUAN PEREZ"), "M")
        self.assertEqual(inferir_genero("MARIA GONZALEZ"), "F")
        self.assertEqual(inferir_genero("FLORENCIA YAMILA CARDOZO"), "F")
        self.assertEqual(inferir_genero("ROBERTO CARLOS"), "M")

    def test_obtener_cuils_candidatos(self):
        c1, c2 = obtener_cuils_candidatos("30112233")
        self.assertTrue(validar_cuit_modulo11(c1))
        self.assertTrue(validar_cuit_modulo11(c2))
        self.assertNotEqual(c1, c2)


if __name__ == "__main__":
    unittest.main()
