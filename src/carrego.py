"""
src/carrego.py — motor de cálculo do carrego do IMA-B 5 e IMA-B.

Fórmula central (carrego nominal ex-ante anualizado):

    ytm_real   = Σ peso_i × taxa_indicativa_i     (média ponderada das taxas reais)
    carrego    = (1 + ytm_real) × (1 + ipca_proj) − 1

Pesos:
    duration  → peso_i = duration_i / Σ duration_j   (proxy para peso de mercado)
    equal     → peso_i = 1 / N

IPCA projetado (em ordem de preferência):
    1. Inflação implícita da curva (média ponderada de inflacao_implicita)
    2. BCB Focus fornecido pelo chamador
    3. Fallback hardcoded (4.5%)

Índices suportados via parâmetro `filtrar`:
    filtrar=True   → IMA-B 5 (NTN-Bs com vencimento ≤ IMAB5_CUTOFF_YEARS)
    filtrar=False  → IMA-B   (todos os NTN-Bs, sem corte de prazo)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from config import IMAB5_CUTOFF_YEARS, WORKING_DAYS_YEAR, WEIGHTING

logger = logging.getLogger(__name__)

_IPCA_FALLBACK = 0.045  # 4,5% a.a. — usado só se tudo falhar


# ─────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────

def _cutoff_date(ref: date) -> date:
    """Data-limite do IMA-B 5: N anos à frente de ref."""
    try:
        return ref.replace(year=ref.year + IMAB5_CUTOFF_YEARS)
    except ValueError:
        # 29/fev em ano não-bissexto
        return ref.replace(year=ref.year + IMAB5_CUTOFF_YEARS, day=28)


def _normalizar_decimal(series: pd.Series) -> pd.Series:
    """
    Garante que a série está em forma decimal (0.0615, não 6.15).
    Chama atenção: a heurística é mediana > 1.0 → dividir por 100.
    """
    if series.dropna().empty:
        return series
    if series.dropna().median() > 1.0:
        return series / 100.0
    return series


# ─────────────────────────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────────────────────────

def filtrar_imab5(df: pd.DataFrame, ref_date: Optional[date] = None) -> pd.DataFrame:
    """
    Mantém apenas NTN-Bs que vencem dentro do horizonte IMA-B 5.

    Args:
        df       : DataFrame de collector.fetch_ntnb()
        ref_date : data de referência (default: hoje)
    """
    ref = ref_date or date.today()
    cutoff = _cutoff_date(ref)

    df = df.copy()
    df["data_vencimento"] = pd.to_datetime(df["data_vencimento"]).dt.date
    result = df[df["data_vencimento"] <= cutoff].copy()

    logger.debug(
        f"filtrar_imab5: {len(df)} → {len(result)} títulos "
        f"(venc ≤ {cutoff})"
    )
    return result.reset_index(drop=True)


def calcular_pesos(df: pd.DataFrame, method: str = WEIGHTING) -> pd.DataFrame:
    """
    Adiciona coluna 'peso' ao DataFrame.

    method = "duration" : peso proporcional à duration
    method = "equal"    : pesos iguais
    """
    df = df.copy()

    if method == "duration" and "duration" in df.columns:
        dur = df["duration"].clip(lower=0.01)
        df["peso"] = dur / dur.sum()
    else:
        df["peso"] = 1.0 / len(df)

    return df


def calcular_carrego(
    bonds: pd.DataFrame,
    ref_date: Optional[date] = None,
    ipca_focus: Optional[float] = None,
    di_annual: Optional[float] = None,
    filtrar: bool = True,
) -> tuple:
    """
    Pipeline completo: filtra → pondera → calcula carrego.

    Args:
        bonds      : DataFrame de collector.fetch_ntnb() (sem filtro prévio)
        ref_date   : data de referência (default: hoje)
        ipca_focus : IPCA projetado Focus em DECIMAL (opcional)
        di_annual  : CDI anualizado em DECIMAL (opcional, para prêmio)
        filtrar    : True → IMA-B 5 (vencimento ≤ 5 anos)
                     False → IMA-B (todos os NTN-Bs)

    Returns:
        (snapshot_dict, bonds_df) — taxas em % a.a. (ex: 6.15 para IPCA+6,15%).

    Raises:
        ValueError se não sobrar nenhum título após o filtro.
    """
    ref = ref_date or date.today()

    # 1. Filtrar universo
    if filtrar:
        b = filtrar_imab5(bonds, ref)
        indice_label = "IMA-B 5"
    else:
        b = bonds.copy()
        b["data_vencimento"] = pd.to_datetime(b["data_vencimento"]).dt.date
        indice_label = "IMA-B"

    if b.empty:
        raise ValueError(
            f"Nenhuma NTN-B no universo {indice_label} para {ref}. "
            "Verifique se há dados no DataFrame."
        )

    # 2. Normalizar taxas para decimal
    b["taxa_indicativa"] = _normalizar_decimal(b["taxa_indicativa"])
    if "inflacao_implicita" in b.columns:
        b["inflacao_implicita"] = _normalizar_decimal(b["inflacao_implicita"])

    # 3. Calcular pesos
    b = calcular_pesos(b)

    # 4. YTM real ponderado
    ytm_real: float = (b["peso"] * b["taxa_indicativa"]).sum()

    # 5. IPCA projetado
    #    Prioridade: inflação implícita da curva > Focus > fallback
    if "inflacao_implicita" in b.columns and b["inflacao_implicita"].notna().any():
        ipca_implicita: float = (b["peso"] * b["inflacao_implicita"]).sum()
        ipca_fonte = "implicita"
    else:
        ipca_implicita = None
        ipca_fonte = None

    if ipca_implicita is not None:
        ipca_proj = ipca_implicita
        ipca_fonte = "implicita"
    elif ipca_focus is not None:
        ipca_proj = ipca_focus
        ipca_fonte = "focus"
    else:
        ipca_proj = _IPCA_FALLBACK
        ipca_fonte = "fallback"
        logger.warning(f"IPCA projetado não disponível — usando fallback {_IPCA_FALLBACK:.1%}")

    # 6. Carrego nominal ex-ante
    carrego_nominal: float = (1 + ytm_real) * (1 + ipca_proj) - 1

    # 7. Carrego diário (base 252 d.u.)
    carrego_diario: float = (
        (1 + ytm_real) ** (1 / WORKING_DAYS_YEAR)
        * (1 + ipca_proj) ** (1 / WORKING_DAYS_YEAR)
        - 1
    )

    # 8. Prêmio vs CDI
    premio_vs_cdi: Optional[float] = (
        (carrego_nominal - di_annual) if di_annual is not None else None
    )

    snapshot = {
        "data":             ref,
        # ── taxas em % a.a. ──────────────────────────────────────
        "ytm_real":         round(ytm_real * 100, 4),
        "ipca_proj":        round(ipca_proj * 100, 4),
        "carrego_nominal":  round(carrego_nominal * 100, 4),
        "carrego_diario":   round(carrego_diario * 100, 6),
        "cdi_anual":        round(di_annual * 100, 4) if di_annual is not None else None,
        "premio_vs_cdi":    round(premio_vs_cdi * 100, 4) if premio_vs_cdi is not None else None,
        # ── metadados ────────────────────────────────────────────
        "n_bonds":          len(b),
        "metodo_peso":      WEIGHTING,
        "fonte_ipca":       ipca_fonte,
    }

    logger.info(
        f"Carrego {ref}: IPCA+{ytm_real:.2%} real | "
        f"carrego {carrego_nominal:.2%} nom | "
        f"prêmio {premio_vs_cdi:+.2%} vs CDI"
        if premio_vs_cdi is not None else
        f"Carrego {ref}: IPCA+{ytm_real:.2%} real | {carrego_nominal:.2%} nom"
    )

    return snapshot, b  # devolve também o DataFrame com pesos (para persistência)
