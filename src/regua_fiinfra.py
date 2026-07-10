"""
Motor da Regua de Ciclo para FI-Infra listados.

Convencoes de unidades:
  - Taxas reais e descontos em % ou p.p. (ex: 6.5, nao 0.065)
  - Spread em bps (ex: 100, nao 1.00)
  - Aliquota em decimal (ex: 0.15)
"""
from __future__ import annotations

import math
from statistics import median
from typing import Iterable, Optional


DEFAULT_THRESHOLDS = {
    "juro_real_caro": 5.0,
    "juro_real_barato": 6.5,
    "spread_caro": 30.0,
    "spread_barato": 100.0,
    "excesso_caro": 0.0,
    "excesso_barato": 4.0,
}

FUNDOS_PADRAO = ("IFRA11", "BDIF11", "KDIF11", "JURO11")

MANDATO_CAIXA = "Caixa oportunista"
MANDATO_JURO_REAL = "Juro real"
MANDATO_RENDA = "Renda"
MANDATOS = (MANDATO_CAIXA, MANDATO_JURO_REAL, MANDATO_RENDA)

ZONA_COMPRAR = "COMPRAR"
ZONA_CARREGAR = "CARREGAR"
ZONA_REDUZIR = "REDUZIR"

ESTADO_BARATO = "BARATO"
ESTADO_NEUTRO = "NEUTRO"
ESTADO_CARO = "CARO"


def validar_thresholds(thresholds: Optional[dict] = None) -> dict:
    """Combina e valida limiares, exigindo ``caro < barato`` em cada sinal."""
    limiares = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    pares = (
        ("juro_real_caro", "juro_real_barato", "juro real"),
        ("spread_caro", "spread_barato", "spread"),
        ("excesso_caro", "excesso_barato", "excesso de desconto"),
    )
    for chave_caro, chave_barato, nome in pares:
        caro = _to_float(limiares.get(chave_caro))
        barato = _to_float(limiares.get(chave_barato))
        if caro is None or barato is None:
            raise ValueError(f"Os limiares de {nome} precisam ser numeros validos.")
        if caro >= barato:
            raise ValueError(f"Em {nome}, o limiar caro precisa ser menor que o barato.")
        limiares[chave_caro] = caro
        limiares[chave_barato] = barato
    return limiares


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Limita um valor ao intervalo definido."""
    return max(lower, min(upper, value))


def normalizar_sinal(valor: float, caro: float, barato: float) -> float:
    """
    Converte um sinal em posicao 0-1, onde 0 e barato e 1 e caro.

    A formula segue a especificacao:
        pos = clamp((barato - valor) / (barato - caro))
    """
    if barato == caro:
        raise ValueError("Os limiares caro e barato precisam ser diferentes.")
    return clamp((barato - valor) / (barato - caro))


def classificar_posicao(posicao: float) -> str:
    """Classifica a posicao normalizada em BARATO, NEUTRO ou CARO."""
    if posicao < 0.33:
        return ESTADO_BARATO
    if posicao > 0.67:
        return ESTADO_CARO
    return ESTADO_NEUTRO


def calcular_desconto_observado(cota_mercado: float, cota_patrimonial: float) -> Optional[float]:
    """Calcula o desconto observado em p.p."""
    if cota_patrimonial is None or cota_patrimonial <= 0:
        return None
    if cota_mercado is None or cota_mercado <= 0:
        return None
    return (1 - cota_mercado / cota_patrimonial) * 100


def calcular_desconto_justo(taxa_total_aa: float, duration: float) -> Optional[float]:
    """Aproximacao de VP das taxas: taxa total anual vezes duration."""
    if taxa_total_aa is None or duration is None or duration <= 0:
        return None
    return taxa_total_aa * duration


def calcular_excesso_desconto(
    cota_mercado: float,
    cota_patrimonial: float,
    taxa_total_aa: float,
    duration: float,
) -> Optional[float]:
    """Calcula o desconto observado menos o desconto justo."""
    observado = calcular_desconto_observado(cota_mercado, cota_patrimonial)
    justo = calcular_desconto_justo(taxa_total_aa, duration)
    if observado is None or justo is None:
        return None
    return observado - justo


def preparar_fundos(fundos: Iterable[dict]) -> tuple[list[dict], Optional[float], Optional[float]]:
    """
    Calcula descontos por fundo.

    Returns:
        (linhas, excesso_mediano, duration_mediana)
    """
    linhas = []
    excessos_validos = []
    durations_validas = []

    for fundo in fundos:
        ticker = str(fundo.get("ticker", "")).upper().strip()
        cota_mercado = _to_float(fundo.get("cota_mercado"))
        cota_patrimonial = _to_float(fundo.get("cota_patrimonial"))
        taxa_total_aa = _to_float(fundo.get("taxa_total_aa"))
        duration = _to_float(fundo.get("duration"))

        desconto_observado = calcular_desconto_observado(cota_mercado, cota_patrimonial)
        desconto_justo = calcular_desconto_justo(taxa_total_aa, duration)
        excesso_desconto = None
        if desconto_observado is not None and desconto_justo is not None:
            excesso_desconto = desconto_observado - desconto_justo
            excessos_validos.append(excesso_desconto)

        if duration is not None and duration > 0:
            durations_validas.append(duration)

        linhas.append({
            "ticker": ticker,
            "cota_mercado": cota_mercado,
            "cota_patrimonial": cota_patrimonial,
            "taxa_total_aa": taxa_total_aa,
            "duration": duration,
            "desconto_observado": desconto_observado,
            "desconto_justo": desconto_justo,
            "excesso_desconto": excesso_desconto,
        })

    excesso_mediano = median(excessos_validos) if excessos_validos else None
    duration_mediana = median(durations_validas) if durations_validas else None
    return linhas, excesso_mediano, duration_mediana


def avaliar_sinais(
    juro_real: float,
    spread: float,
    excesso_desconto: float,
    thresholds: Optional[dict] = None,
) -> dict:
    """Avalia os tres sinais e devolve estados, posicoes e zona."""
    limiares = validar_thresholds(thresholds)
    specs = {
        "juro_real": (juro_real, limiares["juro_real_caro"], limiares["juro_real_barato"]),
        "spread": (spread, limiares["spread_caro"], limiares["spread_barato"]),
        "excesso_desconto": (excesso_desconto, limiares["excesso_caro"], limiares["excesso_barato"]),
    }

    posicoes = {}
    estados = {}
    for nome, (valor, caro, barato) in specs.items():
        posicao = normalizar_sinal(valor, caro, barato)
        posicoes[nome] = posicao
        estados[nome] = classificar_posicao(posicao)

    zona = decidir_zona(estados.values())
    return {
        "zona": zona,
        "estados": estados,
        "posicoes": posicoes,
        "thresholds": limiares,
    }


def decidir_zona(estados: Iterable[str]) -> str:
    """Aplica a regra: tres baratos compram, tres caros reduzem, mistura carrega."""
    estados = list(estados)
    if estados and all(e == ESTADO_BARATO for e in estados):
        return ZONA_COMPRAR
    if estados and all(e == ESTADO_CARO for e in estados):
        return ZONA_REDUZIR
    return ZONA_CARREGAR


def calcular_yield_fundo_real(
    ntnb: float,
    spread_bps: float,
    excesso_desconto: float,
    duration: Optional[float],
) -> Optional[float]:
    """Aproxima o yield real do fundo em % a.a."""
    if ntnb is None or spread_bps is None:
        return None
    premio_desconto = 0.0
    if duration is not None and duration > 0 and excesso_desconto is not None:
        premio_desconto = max(0.0, excesso_desconto) / duration
    return ntnb + spread_bps / 100 + premio_desconto


def calcular_cdi_liquido_real(cdi: float, aliquota: float, inflacao_implicita: float) -> Optional[float]:
    """Calcula CDI liquido deflacionado em % a.a."""
    if cdi is None or inflacao_implicita is None:
        return None
    if aliquota is None or not 0 <= aliquota <= 1:
        raise ValueError("A aliquota precisa estar entre 0 e 1.")
    if inflacao_implicita <= -100:
        raise ValueError("A inflacao implicita precisa ser maior que -100%.")
    cdi_decimal = cdi / 100
    inflacao_decimal = inflacao_implicita / 100
    liquido = ((1 + cdi_decimal * (1 - aliquota)) / (1 + inflacao_decimal)) - 1
    return liquido * 100


def recomendar_execucao(
    zona: str,
    mandato: str,
    yield_fundo_real: Optional[float],
    alternativa_liquida_real: Optional[float],
) -> dict:
    """Transforma a zona em acao operacional respeitando mandato e filtro de carrego."""
    if zona == ZONA_COMPRAR:
        return {
            "acao": "Compra escalonada",
            "destino": "FI-Infra",
            "bloqueada": False,
            "mensagem": "Abrir 3-4 tranches ao longo de semanas ou meses.",
        }

    if zona == ZONA_CARREGAR:
        return {
            "acao": "Carregar",
            "destino": "Manter posicao",
            "bloqueada": False,
            "mensagem": "Estado normal da estrategia: colher carrego e aguardar extremos.",
        }

    if mandato == MANDATO_RENDA:
        return {
            "acao": "Alocar fluxo",
            "destino": "Soberano/colchao para dinheiro novo",
            "bloqueada": False,
            "mensagem": "Mandato de renda nao gera venda; direciona dividendos e aportes.",
        }

    filtro_ok = (
        yield_fundo_real is not None
        and alternativa_liquida_real is not None
        and yield_fundo_real <= alternativa_liquida_real
    )
    if not filtro_ok:
        return {
            "acao": "Carregar",
            "destino": "Manter posicao",
            "bloqueada": True,
            "mensagem": "Zona cara, mas venda bloqueada pelo filtro de carrego.",
        }

    destino = "CDI liquido" if mandato == MANDATO_CAIXA else "IMAB11"
    return {
        "acao": "Reducao parcial",
        "destino": destino,
        "bloqueada": False,
        "mensagem": "Reduzir 1/2 a 2/3 nos extremos caros e manter nucleo.",
    }


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        if value == "":
            return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None
