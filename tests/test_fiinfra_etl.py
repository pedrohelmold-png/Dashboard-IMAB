from datetime import date
import unittest
from unittest.mock import patch

from fiinfra_etl import coletar_observacoes


class FiInfraEtlTests(unittest.TestCase):
    def test_persiste_resultado_parcial_quando_macro_falha(self):
        fundos = {
            "fundos": [{"ticker": "IFRA11", "cota_mercado": 94.0}],
            "fontes_tentadas": {"b3": ["B3 COTAHIST 2026"]},
            "erros": {"cvm": "indisponivel"},
        }
        with patch("fiinfra_etl.fetch_fiinfra_macro", side_effect=RuntimeError("macro fora")), patch(
            "fiinfra_etl.fetch_fiinfra_fundos_result", return_value=fundos
        ), patch("fiinfra_etl.save_fiinfra_collection_observation") as save:
            batch = coletar_observacoes(date(2026, 7, 13))

        self.assertEqual(batch["fundos"][0]["ticker"], "IFRA11")
        self.assertIn("macro: macro fora", batch["erros"])
        self.assertIn("cvm: indisponivel", batch["erros"])
        save.assert_called_once_with(batch)


if __name__ == "__main__":
    unittest.main()
