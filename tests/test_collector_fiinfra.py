from datetime import date
import io
import unittest
from unittest.mock import patch
import zipfile
import pandas as pd

from src.collector import (
    FIINFRA_FUNDOS,
    fetch_cotas_cvm,
    fetch_cotacoes_b3,
    fetch_fiinfra_macro,
    fetch_ipca_focus_info,
    selecionar_ntnb_referencia,
)


def _zip_bytes(filename, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, content)
    return buffer.getvalue()


def _cotahist_line(data_ref, ticker, preco):
    line = [" "] * 245
    line[0:2] = "01"
    line[2:10] = data_ref.strftime("%Y%m%d")
    line[12:24] = f"{ticker:<12}"
    line[108:121] = f"{round(preco * 100):013d}"
    return "".join(line)


class CollectorFiInfraTests(unittest.TestCase):
    def test_cadastro_mestre_tem_os_quatro_fundos(self):
        self.assertEqual(set(FIINFRA_FUNDOS), {"IFRA11", "BDIF11", "KDIF11", "JURO11"})
        self.assertEqual(FIINFRA_FUNDOS["KDIF11"], "26.324.298/0001-89")

    def test_ntnb_escolhe_duration_mais_proxima(self):
        df = pd.DataFrame([
            {"data_vencimento": date(2035, 5, 15), "duration": 6.5, "taxa_indicativa": 0.07},
            {"data_vencimento": date(2040, 8, 15), "duration": 8.8, "taxa_indicativa": 0.075},
            {"data_vencimento": date(2060, 8, 15), "duration": 13.0, "taxa_indicativa": 0.074},
        ])
        row = selecionar_ntnb_referencia(df, target_duration=8.0)
        self.assertEqual(row["data_vencimento"], date(2040, 8, 15))

    @patch("src.collector._fetch_focus_12m_rows")
    def test_focus_12m_escolhe_ultima_data_ate_referencia(self, rows):
        rows.return_value = [
            {"Data": "2026-07-10", "Mediana": 4.2},
            {"Data": "2026-07-03", "Mediana": 4.1001},
        ]
        info = fetch_ipca_focus_info(date(2026, 7, 9))
        self.assertEqual(info["data"], date(2026, 7, 3))
        self.assertAlmostEqual(info["valor"], 4.1001)

    @patch("src.collector.fetch_ipca_focus_info")
    @patch("src.collector.fetch_di_over")
    @patch("src.collector.fetch_ntnb")
    def test_macro_recua_e_marca_dado_defasado(self, ntnb, di, focus):
        vazio = pd.DataFrame()
        curva = pd.DataFrame([{
            "data_vencimento": date(2040, 8, 15), "duration": 8.8,
            "taxa_indicativa": 0.075, "inflacao_implicita": 0.05,
        }])
        ntnb.side_effect = [vazio, curva]
        di.side_effect = [None, 0.1415]
        focus.return_value = {
            "valor": 4.1, "data": date(2026, 7, 3), "fonte": "BCB Focus"
        }
        result = fetch_fiinfra_macro(date(2026, 7, 10), target_duration=8.0)
        self.assertEqual(result["ntnb_data"], date(2026, 7, 9))
        self.assertEqual(result["ntnb_status"], "DEFASADO")
        self.assertEqual(result["cdi_status"], "DEFASADO")
        self.assertEqual(result["ntnb_vencimento"], date(2040, 8, 15))
        self.assertEqual(result["ipca_focus"], 4.1)
        self.assertEqual(result["ipca_focus_status"], "DEFASADO")

    @patch("src.collector._download_zip")
    def test_b3_escolhe_ultimo_fechamento_ate_data(self, download):
        content = "\n".join([
            _cotahist_line(date(2026, 7, 8), "IFRA11", 92.10),
            _cotahist_line(date(2026, 7, 9), "IFRA11", 93.31),
            _cotahist_line(date(2026, 7, 11), "IFRA11", 99.99),
        ])
        download.return_value = _zip_bytes("COTAHIST_A2026.TXT", content.encode("latin-1"))
        result = fetch_cotacoes_b3(date(2026, 7, 10), ["IFRA11"])
        self.assertEqual(result["IFRA11"]["data"], date(2026, 7, 9))
        self.assertAlmostEqual(result["IFRA11"]["valor"], 93.31)

    @patch("src.collector._download_zip")
    def test_cvm_aceita_decimal_com_ponto_e_virgula(self, download):
        cnpj = FIINFRA_FUNDOS["IFRA11"]
        csv = (
            "CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA\n"
            f"{cnpj};2026-07-08;94.8697347\n"
            f"{cnpj};2026-07-09;95,25\n"
        )
        download.return_value = _zip_bytes("inf_diario.csv", csv.encode("latin-1"))
        result = fetch_cotas_cvm(date(2026, 7, 10), {"IFRA11": cnpj})
        self.assertEqual(result["IFRA11"]["data"], date(2026, 7, 9))
        self.assertAlmostEqual(result["IFRA11"]["valor"], 95.25)


if __name__ == "__main__":
    unittest.main()
