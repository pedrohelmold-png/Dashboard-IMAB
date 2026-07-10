from datetime import date
import unittest

import pandas as pd

from src.carrego import calcular_carrego


class CarregoIpcaTests(unittest.TestCase):
    def setUp(self):
        self.bonds = pd.DataFrame([
            {"data_vencimento": date(2028, 5, 15), "taxa_indicativa": 0.08,
             "duration": 2.0, "inflacao_implicita": 0.055},
            {"data_vencimento": date(2030, 8, 15), "taxa_indicativa": 0.075,
             "duration": 4.0, "inflacao_implicita": 0.065},
        ])

    def test_focus_tem_prioridade_e_implicita_fica_separada(self):
        snap, _ = calcular_carrego(
            self.bonds, ref_date=date(2026, 7, 10), ipca_focus=0.041, filtrar=True
        )
        self.assertEqual(snap["fonte_ipca"], "focus")
        self.assertEqual(snap["ipca_proj"], 4.1)
        self.assertEqual(snap["ipca_focus"], 4.1)
        self.assertNotEqual(snap["ipca_implicita"], snap["ipca_focus"])

    def test_implicita_e_fallback_identificado_sem_focus(self):
        snap, _ = calcular_carrego(
            self.bonds, ref_date=date(2026, 7, 10), ipca_focus=None, filtrar=True
        )
        self.assertEqual(snap["fonte_ipca"], "implicita_fallback")
        self.assertEqual(snap["ipca_proj"], snap["ipca_implicita"])
        self.assertIsNone(snap["ipca_focus"])


if __name__ == "__main__":
    unittest.main()
