from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.db import (
    get_ultimo_fiinfra_snapshot,
    init_db_fiinfra,
    insert_fiinfra_tranche,
    load_fiinfra_fundos,
    load_fiinfra_snapshots,
    load_fiinfra_thresholds,
    load_fiinfra_tranches,
    save_fiinfra_thresholds,
    upsert_fiinfra_snapshot,
)


class DbFiInfraTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "fiinfra.db"
        init_db_fiinfra(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_thresholds_sao_inicializados_e_atualizados(self):
        thresholds = load_fiinfra_thresholds(self.db_path)
        self.assertEqual(thresholds["juro_real_barato"], 6.5)
        save_fiinfra_thresholds({"juro_real_barato": 7.0}, self.db_path)
        self.assertEqual(load_fiinfra_thresholds(self.db_path)["juro_real_barato"], 7.0)
        with self.assertRaises(ValueError):
            save_fiinfra_thresholds(
                {"juro_real_caro": 7.0, "juro_real_barato": 6.0}, self.db_path
            )

    def test_snapshot_e_fundos_sao_substituidos_atomicamente(self):
        snapshot = self._snapshot()
        fundos = [self._fundo("IFRA11"), self._fundo("BDIF11")]
        upsert_fiinfra_snapshot(snapshot, fundos, self.db_path)
        snapshot["observacao"] = "revisado"
        upsert_fiinfra_snapshot(snapshot, [self._fundo("IFRA11")], self.db_path)

        ultimo = get_ultimo_fiinfra_snapshot(self.db_path)
        self.assertEqual(ultimo["observacao"], "revisado")
        self.assertEqual(ultimo["venda_bloqueada"], 0)
        self.assertIn("ntnb_status", ultimo)
        self.assertEqual(ultimo["metodologia_version"], "v1")
        self.assertEqual(ultimo["cobertura_fundos"], 4)
        self.assertEqual(ultimo["ntnb_fonte"], "ANBIMA")
        self.assertEqual(ultimo["ntnb_override"], 1)
        self.assertEqual(ultimo["inflacao_usada_fonte"], "focus")
        self.assertEqual(len(load_fiinfra_snapshots(db_path=self.db_path)), 1)
        fundos_salvos = load_fiinfra_fundos(date(2026, 7, 10), self.db_path)
        self.assertEqual(fundos_salvos["ticker"].tolist(), ["IFRA11"])
        self.assertEqual(fundos_salvos.iloc[0]["cota_mercado_fonte"], "B3")
        self.assertEqual(fundos_salvos.iloc[0]["cota_mercado_override"], 0)

    def test_tranches_sao_ordenadas_da_mais_recente(self):
        for data_ref, ticker in [(date(2026, 7, 9), "IFRA11"), (date(2026, 7, 10), "BDIF11")]:
            insert_fiinfra_tranche({"tipo": "Compra", "data": data_ref, "ticker": ticker,
                                    "qtd": 10, "preco": 95, "observacao": "teste"}, self.db_path)
        tranches = load_fiinfra_tranches(db_path=self.db_path)
        self.assertEqual(tranches.iloc[0]["ticker"], "BDIF11")

    @staticmethod
    def _fundo(ticker):
        return {"ticker": ticker, "cota_mercado": 90,
                "cota_mercado_original": 90, "cota_mercado_fonte": "B3",
                "cota_mercado_status": "DENTRO_SLA", "cota_mercado_override": False,
                "cota_patrimonial": 100, "cota_patrimonial_original": 100,
                "cota_patrimonial_fonte": "CVM", "cota_patrimonial_status": "DENTRO_SLA",
                "cota_patrimonial_override": False,
                "taxa_total_aa": 0.5, "duration": 8, "desconto_observado": 10,
                "desconto_justo": 4, "excesso_desconto": 6, "elegivel": True}

    @staticmethod
    def _snapshot():
        return {"data": date(2026, 7, 10), "ntnb": 6.5, "spread": 100,
                "metodologia_version": "v1", "cobertura_fundos": 4,
                "juro_real_caro_ref": 5.0, "juro_real_barato_ref": 6.5,
                "spread_caro_ref": 50, "spread_barato_ref": 100,
                "excesso_caro_ref": 0, "excesso_barato_ref": 3,
                "ntnb_original": 6.4, "ntnb_fonte": "ANBIMA", "ntnb_override": True,
                "excesso_mediano": 6, "duration_mediana": 8, "zona": "COMPRAR",
                "juro_estado": "BARATO", "spread_estado": "BARATO", "excesso_estado": "BARATO",
                "juro_pos": 0, "spread_pos": 0, "excesso_pos": 0, "mandato": "Juro real",
                "cdi": 12, "cdi_original": 12, "cdi_fonte": "BCB",
                "cdi_override": False, "aliquota": 0.15, "inflacao_implicita": 5,
                "inflacao_original": 5, "inflacao_fonte": "ANBIMA",
                "inflacao_override": False,
                "ipca_focus": 4.1, "ipca_focus_data": date(2026, 7, 3),
                "ipca_focus_original": 4.1, "ipca_focus_fonte": "BCB Focus",
                "ipca_focus_override": False, "ipca_focus_status": "DEFASADO",
                "inflacao_usada": 4.1,
                "inflacao_usada_fonte": "focus",
                "alternativa_liquida_real": 5.5, "yield_fundo_real": 8,
                "acao": "Compra escalonada", "destino": "FI-Infra",
                "venda_bloqueada": False, "observacao": "inicial"}


if __name__ == "__main__":
    unittest.main()
