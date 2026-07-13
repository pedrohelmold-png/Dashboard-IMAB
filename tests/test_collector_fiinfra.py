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
    fetch_fiinfra_fundos_result,
    fetch_fiinfra_premissas,
    fetch_ipca_focus_info,
    selecionar_ntnb_referencia,
    _download_zip,
    clear_collector_cache,
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
    def tearDown(self):
        clear_collector_cache()

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
        self.assertEqual(result["ntnb_status"], "DENTRO_SLA")
        self.assertEqual(result["cdi_status"], "DENTRO_SLA")
        self.assertEqual(result["ntnb_vencimento"], date(2040, 8, 15))
        self.assertEqual(result["ipca_focus"], 4.1)
        self.assertEqual(result["ipca_focus_status"], "DENTRO_SLA")

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
    def test_b3_recua_para_ano_anterior_na_virada(self, download):
        atual = _zip_bytes("COTAHIST_A2026.TXT", b"")
        anterior = _zip_bytes(
            "COTAHIST_A2025.TXT",
            _cotahist_line(date(2025, 12, 30), "IFRA11", 91.42).encode("latin-1"),
        )
        download.side_effect = lambda url, force_refresh=False: (
            atual if "A2026" in url else anterior
        )

        result = fetch_cotacoes_b3(date(2026, 1, 5), ["IFRA11"])

        self.assertEqual(result["IFRA11"]["data"], date(2025, 12, 30))
        self.assertAlmostEqual(result["IFRA11"]["valor"], 91.42)
        self.assertEqual(download.call_count, 2)

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

    @patch("src.collector._download_zip")
    def test_cvm_recua_para_mes_anterior_na_virada(self, download):
        cnpj = FIINFRA_FUNDOS["IFRA11"]
        agosto = _zip_bytes(
            "inf_diario_fi_202608.csv",
            "CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA\n".encode("latin-1"),
        )
        julho = _zip_bytes(
            "inf_diario_fi_202607.csv",
            (
                "CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA\n"
                f"{cnpj};2026-07-31;96,10\n"
            ).encode("latin-1"),
        )
        download.side_effect = lambda url, force_refresh=False: (
            agosto if "202608" in url else julho
        )

        result = fetch_cotas_cvm(date(2026, 8, 3), {"IFRA11": cnpj})

        self.assertEqual(result["IFRA11"]["data"], date(2026, 7, 31))
        self.assertAlmostEqual(result["IFRA11"]["valor"], 96.10)
        self.assertEqual(result["IFRA11"]["fonte"], "CVM Informe Diario 2026-07")

    @patch("src.collector._download_zip")
    def test_cvm_falha_mes_atual_preserva_mes_anterior(self, download):
        cnpj = FIINFRA_FUNDOS["IFRA11"]
        julho = _zip_bytes(
            "inf_diario_fi_202607.csv",
            (
                "CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA\n"
                f"{cnpj};2026-07-31;96,10\n"
            ).encode("latin-1"),
        )

        def responder(url, force_refresh=False):
            if "202608" in url:
                raise RuntimeError("arquivo ainda nao publicado")
            return julho

        download.side_effect = responder
        result = fetch_cotas_cvm(date(2026, 8, 3), {"IFRA11": cnpj})

        self.assertEqual(result["IFRA11"]["data"], date(2026, 7, 31))

    @patch("src.collector.urllib.request.urlopen")
    def test_cache_tem_refresh_forcado(self, urlopen):
        urlopen.side_effect = [io.BytesIO(b"primeiro"), io.BytesIO(b"segundo")]
        self.assertEqual(_download_zip("https://exemplo.test/dados.zip"), b"primeiro")
        self.assertEqual(_download_zip("https://exemplo.test/dados.zip"), b"primeiro")
        self.assertEqual(
            _download_zip("https://exemplo.test/dados.zip", force_refresh=True),
            b"segundo",
        )
        self.assertEqual(urlopen.call_count, 2)

    @patch("src.collector.fetch_cotas_cvm")
    @patch("src.collector.fetch_cotacoes_b3")
    def test_falha_b3_preserva_resultado_cvm(self, b3, cvm):
        b3.side_effect = RuntimeError("B3 indisponivel")
        cvm.return_value = {
            "IFRA11": {"valor": 95.0, "data": date(2026, 7, 9), "fonte": "CVM"}
        }
        with patch("src.collector.fetch_fiinfra_premissas", return_value={
            "premissas": {}, "fontes_tentadas": {}, "erros": {},
        }):
            result = fetch_fiinfra_fundos_result(
                date(2026, 7, 10), fundos={"IFRA11": FIINFRA_FUNDOS["IFRA11"]}
            )
        self.assertIn("b3", result["erros"])
        self.assertIn("B3 COTAHIST 2026", result["fontes_tentadas"]["b3"])
        self.assertIn("CVM Informe Diario 2026-07", result["fontes_tentadas"]["cvm"])
        self.assertIsNone(result["fundos"][0]["cota_mercado"])
        self.assertEqual(result["fundos"][0]["cota_patrimonial"], 95.0)

    @patch("src.collector._download_binary")
    def test_premissas_oficiais_sao_extraidas_com_data_base(self, download):
        ifra = (
            "Informações sobre a carteira (em 30/06/2026) "
            "Duration dos títulos privados (média da carteira) 5,29 anos "
            "Taxa de Administração máx.: 0,85% a.a."
        ).encode()
        kdif = (
            'Data de referência: 10/07/26 '
            '<span id="duration-fundo">4,396212</span>'
            '<span id="txaAdm">1.11</span>'
        ).encode()
        juro = (
            "Dados de fechamento do dia 30/01/2026 Taxa de Administração: 1,0% "
            "4,7\n R$ 2,1 91.994"
        ).encode()
        download.side_effect = [ifra, kdif, juro]
        with patch("src.collector._pdf_text", side_effect=[ifra.decode(), juro.decode()]):
            result = fetch_fiinfra_premissas(date(2026, 7, 13))

        self.assertEqual(result["premissas"]["IFRA11"]["data"], date(2026, 6, 30))
        self.assertAlmostEqual(result["premissas"]["KDIF11"]["duration"], 4.396212)
        self.assertAlmostEqual(result["premissas"]["JURO11"]["duration"], 4.7)
        self.assertIn("BDIF11", result["fontes_tentadas"])


if __name__ == "__main__":
    unittest.main()
