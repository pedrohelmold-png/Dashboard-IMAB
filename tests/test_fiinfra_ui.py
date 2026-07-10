from datetime import date, timedelta
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def _macro(ref_date):
    return {
        "data_solicitada": ref_date,
        "ntnb": 7.9,
        "ntnb_status": "ATUALIZADO",
        "cdi": 14.1,
        "cdi_status": "ATUALIZADO",
        "inflacao_implicita": 6.1,
        "inflacao_status": "ATUALIZADO",
        "ipca_focus": 4.1,
        "ipca_focus_status": "ATUALIZADO",
        "fonte": "teste",
    }


def _fundos(ref_date):
    return [{
        "ticker": ticker,
        "cota_mercado": 90.0,
        "cota_patrimonial": 100.0,
        "cota_mercado_data": ref_date,
        "cota_patrimonial_data": ref_date,
        "cota_mercado_status": "ATUALIZADO",
        "cota_patrimonial_status": "ATUALIZADO",
    } for ticker in ("IFRA11", "BDIF11", "KDIF11", "JURO11")]


class FiInfraUiTests(unittest.TestCase):
    def test_troca_de_data_invalida_coleta_anterior(self):
        ref = date.today()
        result = {"fundos": _fundos(ref), "erros": {}, "data_solicitada": ref}
        with patch("src.fiinfra_ui.fetch_fiinfra_macro", return_value=_macro(ref)), patch(
            "src.fiinfra_ui.fetch_fiinfra_fundos_result", return_value=result
        ):
            at = AppTest.from_file("app.py", default_timeout=30).run()
            at.radio[0].set_value("Regua FI-Infra").run()
            [b for b in at.button if b.label == "Atualizar dados oficiais"][0].click().run()
            self.assertFalse(at.exception)
            ntnb = [x for x in at.number_input if x.label == "NTN-B longa (% a.a.)"][0]
            self.assertEqual(ntnb.value, 7.9)

            at.date_input[0].set_value(ref - timedelta(days=1)).run()
            self.assertFalse(at.exception)
            self.assertTrue(any(
                "data mudou" in item.value.lower() for item in at.info
            ))
            ntnb = [x for x in at.number_input if x.label == "NTN-B longa (% a.a.)"][0]
            self.assertNotEqual(ntnb.value, 7.9)

    def test_limiar_invalido_nao_quebra_recomendacao(self):
        ref = date.today()
        result = {"fundos": _fundos(ref), "erros": {}, "data_solicitada": ref}
        with patch("src.fiinfra_ui.fetch_fiinfra_macro", return_value=_macro(ref)), patch(
            "src.fiinfra_ui.fetch_fiinfra_fundos_result", return_value=result
        ):
            at = AppTest.from_file("app.py", default_timeout=30).run()
            at.radio[0].set_value("Regua FI-Infra").run()
            [b for b in at.button if b.label == "Atualizar dados oficiais"][0].click().run()
            caro = [x for x in at.number_input if x.label == "Juro caro"][0]
            barato = [x for x in at.number_input if x.label == "Juro barato"][0]
            caro.set_value(7.0)
            barato.set_value(6.0)
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any("juro real" in item.value for item in at.error))


if __name__ == "__main__":
    unittest.main()
