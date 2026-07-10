from datetime import date, timedelta
import unittest
from unittest.mock import patch

import pandas as pd
from plotly.subplots import make_subplots
from streamlit.testing.v1 import AppTest

from src.fiinfra_ui import (
    _add_signal_trace,
    _confirmar_estimativas_fundos,
    _historical_threshold,
    _snapshot_payload,
)
from src.regua_fiinfra import avaliar_sinais


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

    def test_snapshot_payload_guarda_metodologia_limiares_e_overrides(self):
        ref = date(2026, 7, 10)
        avaliacao = avaliar_sinais(7.0, 120, 5.0)
        payload = _snapshot_payload(
            ref_date=ref,
            ntnb=7.1,
            spread=120,
            excesso_mediano=5.0,
            duration_mediana=8.0,
            cobertura_fundos=4,
            avaliacao=avaliacao,
            mandato="Juro real",
            cdi=14.0,
            aliquota=0.15,
            inflacao_implicita=6.0,
            ipca_focus=4.5,
            inflacao_usada=4.5,
            inflacao_usada_fonte="focus",
            alternativa_liquida_real=6.0,
            yield_fundo_real=8.0,
            execucao={"acao": "Compra", "destino": "FI-Infra", "bloqueada": False},
            observacao="teste",
            auto_macro={
                "ntnb": 7.0,
                "ntnb_fonte": "ANBIMA",
                "ntnb_status": "ATUALIZADO",
                "cdi": 14.0,
                "cdi_fonte": "BCB",
                "cdi_status": "ATUALIZADO",
                "inflacao_implicita": 6.0,
                "inflacao_fonte": "ANBIMA",
                "inflacao_status": "ATUALIZADO",
                "ipca_focus": 4.5,
                "ipca_focus_fonte": "BCB Focus",
                "ipca_focus_status": "DENTRO_SLA",
            },
            collection={"collection_id": "abc", "data_solicitada": ref},
        )

        self.assertEqual(payload["metodologia_version"], "v2")
        self.assertEqual(payload["cobertura_fundos"], 4)
        self.assertEqual(payload["juro_real_barato_ref"], avaliacao["thresholds"]["juro_real_barato"])
        self.assertEqual(payload["collection_id"], "abc")
        self.assertEqual(payload["ntnb_original"], 7.0)
        self.assertTrue(payload["ntnb_override"])
        self.assertFalse(payload["cdi_override"])

    def test_confirmacao_marca_estimativas_como_confirmadas(self):
        fundos = [{
            "ticker": "IFRA11",
            "taxa_total_status": "ESTIMATIVA_NAO_CONFIRMADA",
            "duration_status": "ESTIMATIVA_NAO_CONFIRMADA",
        }]

        confirmados = _confirmar_estimativas_fundos(fundos)

        self.assertEqual(confirmados[0]["taxa_total_status"], "MANUAL_CONFIRMADO")
        self.assertEqual(confirmados[0]["duration_status"], "MANUAL_CONFIRMADO")
        self.assertEqual(fundos[0]["taxa_total_status"], "ESTIMATIVA_NAO_CONFIRMADA")

    def test_historico_usa_limiares_congelados_com_fallback(self):
        hist = pd.DataFrame({
            "data": [date(2026, 7, 3), date(2026, 7, 10)],
            "ntnb": [6.0, 7.0],
            "juro_real_caro_ref": [5.0, None],
            "juro_real_barato_ref": [6.5, 7.0],
        })

        barato = _historical_threshold(hist, "juro_real_barato_ref", 6.6)
        caro = _historical_threshold(hist, "juro_real_caro_ref", 5.2)

        self.assertEqual(barato.tolist(), [6.5, 7.0])
        self.assertEqual(caro.tolist(), [5.0, 5.2])

    def test_trace_historico_desenha_limiares_como_series(self):
        hist = pd.DataFrame({
            "data": [date(2026, 7, 3), date(2026, 7, 10)],
            "ntnb": [6.0, 7.0],
            "juro_real_caro_ref": [5.0, 5.2],
            "juro_real_barato_ref": [6.5, 6.8],
        })
        fig = make_subplots(rows=1, cols=1)

        _add_signal_trace(
            fig, hist, 1, "ntnb", "Juro real", 4.5, 6.0, "%",
            caro_ref_col="juro_real_caro_ref",
            barato_ref_col="juro_real_barato_ref",
        )

        self.assertEqual(len(fig.data), 3)
        self.assertEqual(list(fig.data[1].y), [6.5, 6.8])
        self.assertEqual(list(fig.data[2].y), [5.0, 5.2])


if __name__ == "__main__":
    unittest.main()
