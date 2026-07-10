import math
import unittest
from datetime import date

from src.regua_fiinfra import (
    ESTADO_BARATO,
    ESTADO_CARO,
    ESTADO_NEUTRO,
    MANDATO_CAIXA,
    MANDATO_RENDA,
    ZONA_CARREGAR,
    ZONA_COMPRAR,
    ZONA_REDUZIR,
    avaliar_sinais,
    calcular_cdi_liquido_real,
    calcular_desconto_observado,
    calcular_yield_fundo_real,
    classificar_posicao,
    normalizar_sinal,
    preparar_fundos,
    recomendar_execucao,
    validar_thresholds,
)


class ReguaFiInfraTests(unittest.TestCase):
    def test_normalizacao_e_classificacao_nos_limites(self):
        self.assertEqual(normalizar_sinal(6.5, 5.0, 6.5), 0.0)
        self.assertEqual(normalizar_sinal(5.0, 5.0, 6.5), 1.0)
        self.assertEqual(normalizar_sinal(8.0, 5.0, 6.5), 0.0)
        self.assertEqual(normalizar_sinal(4.0, 5.0, 6.5), 1.0)
        self.assertEqual(classificar_posicao(0.32), ESTADO_BARATO)
        self.assertEqual(classificar_posicao(0.33), ESTADO_NEUTRO)
        self.assertEqual(classificar_posicao(0.67), ESTADO_NEUTRO)
        self.assertEqual(classificar_posicao(0.68), ESTADO_CARO)

    def test_thresholds_invalidos_sao_rejeitados(self):
        with self.assertRaisesRegex(ValueError, "juro real"):
            validar_thresholds({"juro_real_caro": 6.5, "juro_real_barato": 6.5})
        with self.assertRaisesRegex(ValueError, "spread"):
            avaliar_sinais(6.5, 100, 4, {"spread_caro": 110, "spread_barato": 100})

    def test_zonas_barata_cara_e_mista(self):
        self.assertEqual(avaliar_sinais(7.0, 120, 5.0)["zona"], ZONA_COMPRAR)
        self.assertEqual(avaliar_sinais(4.5, 20, -1.0)["zona"], ZONA_REDUZIR)
        self.assertEqual(avaliar_sinais(7.0, 20, 5.0)["zona"], ZONA_CARREGAR)

    def test_preparar_fundos_ignora_dados_invalidos_na_mediana(self):
        fundos, excesso, duration = preparar_fundos([
            {"ticker": "ifra11", "cota_mercado": 90, "cota_patrimonial": 100,
             "taxa_total_aa": 0.5, "duration": 8},
            {"ticker": "bdif11", "cota_mercado": math.nan, "cota_patrimonial": 100,
             "taxa_total_aa": 1, "duration": 10},
        ])
        self.assertEqual(fundos[0]["ticker"], "IFRA11")
        self.assertAlmostEqual(calcular_desconto_observado(90, 100), 10.0)
        self.assertAlmostEqual(excesso, 6.0)
        self.assertEqual(duration, 8.0)
        self.assertIsNone(fundos[1]["excesso_desconto"])

    def test_preparar_fundos_rejeita_valores_infinitos(self):
        fundos, excesso, duration = preparar_fundos([{
            "ticker": "IFRA11", "cota_mercado": math.inf,
            "cota_patrimonial": 100, "taxa_total_aa": 1, "duration": 8,
        }])
        self.assertIsNone(fundos[0]["cota_mercado"])
        self.assertIsNone(excesso)
        self.assertIsNone(duration)

    def test_fundo_com_datas_desalinhadas_e_excluido(self):
        fundos, excesso, duration = preparar_fundos([{
            "ticker": "IFRA11", "cota_mercado": 90, "cota_patrimonial": 100,
            "taxa_total_aa": 0.5, "duration": 8,
            "mercado_data": date(2026, 7, 10),
            "patrimonial_data": date(2026, 7, 7),
        }])
        self.assertFalse(fundos[0]["elegivel"])
        self.assertEqual(fundos[0]["motivo_exclusao"], "datas_b3_cvm_desalinhadas")
        self.assertIsNone(excesso)
        self.assertIsNone(duration)

    def test_override_de_cota_e_identificado(self):
        fundos, _, _ = preparar_fundos([{
            "ticker": "IFRA11", "cota_mercado": 91, "cota_mercado_original": 90,
            "cota_patrimonial": 100, "cota_patrimonial_original": 100,
            "taxa_total_aa": 0.5, "duration": 8,
        }])
        self.assertTrue(fundos[0]["cota_mercado_override"])
        self.assertFalse(fundos[0]["cota_patrimonial_override"])

    def test_carry_e_cdi_real(self):
        self.assertAlmostEqual(calcular_yield_fundo_real(6.0, 100, 4.0, 8.0), 7.5)
        esperado = ((1 + 0.12 * 0.85) / 1.05 - 1) * 100
        self.assertAlmostEqual(calcular_cdi_liquido_real(12, 0.15, 5), esperado)
        with self.assertRaises(ValueError):
            calcular_cdi_liquido_real(12, 1.5, 5)

    def test_recomendacao_respeita_mandato_e_filtro_de_carrego(self):
        compra = recomendar_execucao(ZONA_COMPRAR, MANDATO_CAIXA, 8, 6)
        self.assertEqual(compra["acao"], "Compra escalonada")
        renda = recomendar_execucao(ZONA_REDUZIR, MANDATO_RENDA, 5, 6)
        self.assertEqual(renda["acao"], "Alocar fluxo")
        bloqueada = recomendar_execucao(ZONA_REDUZIR, MANDATO_CAIXA, 7, 6)
        self.assertTrue(bloqueada["bloqueada"])
        reducao = recomendar_execucao(ZONA_REDUZIR, MANDATO_CAIXA, 5, 6)
        self.assertFalse(reducao["bloqueada"])
        self.assertEqual(reducao["destino"], "CDI liquido")


if __name__ == "__main__":
    unittest.main()
