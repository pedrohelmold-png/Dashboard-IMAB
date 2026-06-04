"""
src/collector.py — wrappers sobre a biblioteca pyield.

Toda chamada externa (ANBIMA, BCB, B3) passa por aqui.
Funções retornam pandas DataFrames ou scalars Python; nunca levantam exceção
para dados indisponíveis (feriado, fim-de-semana, etc.) — retornam None ou
DataFrame vazio, deixando o ETL decidir o que fazer.

Fontes:
  ntnb.dados()          → ANBIMA taxas indicativas NTN-B
  di_over()             → BCB/B3 taxa DI Over anualizada (≈ CDI)
  ipca.taxa_projetada() → BCB Focus, IPCA 12m projetado
  du.gerar()            → calendário de dias úteis BR
"""
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── importação lazy (permite import mesmo antes do pip install) ──
def _yd():
    try:
        import pyield as yd
        return yd
    except ImportError:
        raise ImportError("pyield não instalado. Execute: pip install pyield")


# ─────────────────────────────────────────────────────────────────
# NTN-B (fonte: ANBIMA)
# ─────────────────────────────────────────────────────────────────

def fetch_ntnb(ref_date: date) -> pd.DataFrame:
    """
    Retorna DataFrame com as NTN-Bs do dia `ref_date` conforme ANBIMA.

    Colunas garantidas após normalização:
      data_referencia   (date)
      data_vencimento   (date)
      taxa_indicativa   (float, DECIMAL — ex: 0.0615 para IPCA+6,15%)
      duration          (float, anos)
      dias_uteis        (int)
      inflacao_implicita (float, DECIMAL — ex: 0.045 para 4,5%)

    Retorna DataFrame vazio se não houver dado (feriado, fim de semana).
    """
    yd = _yd()
    date_str = ref_date.strftime("%d-%m-%Y")
    try:
        result = yd.ntnb.dados(date_str)
    except Exception as exc:
        logger.debug(f"ntnb.dados({date_str}) → sem dado: {exc}")
        return pd.DataFrame()

    if result is None:
        return pd.DataFrame()

    # pyield devolve Polars DataFrame — converter para pandas
    try:
        df = result.to_pandas() if hasattr(result, "to_pandas") else pd.DataFrame(result)
    except Exception as exc:
        logger.error(f"Falha ao converter DataFrame pyield: {exc}")
        return pd.DataFrame()

    if df.empty:
        return df

    # ── Normalizar taxa_indicativa para DECIMAL ─────────────────
    # pyield pode devolver em % (6.15) ou decimal (0.0615)
    # Heurística: se mediana > 1.0, está em percentual → dividir por 100
    for col in ("taxa_indicativa", "taxa_compra", "taxa_venda", "inflacao_implicita"):
        if col in df.columns:
            if df[col].dropna().median() > 1.0:
                df[col] = df[col] / 100.0

    # ── Garantir tipo date em data_vencimento ───────────────────
    if "data_vencimento" in df.columns:
        df["data_vencimento"] = pd.to_datetime(df["data_vencimento"]).dt.date

    logger.info(f"fetch_ntnb({date_str}): {len(df)} títulos")
    return df


# ─────────────────────────────────────────────────────────────────
# DI Over / CDI (fonte: BCB / B3)
# ─────────────────────────────────────────────────────────────────

def fetch_di_over(ref_date: date) -> Optional[float]:
    """
    Retorna a taxa DI Over ANUALIZADA em decimal para `ref_date`.
    Ex: 0.1465 para CDI 14,65% a.a.

    Retorna None se indisponível.
    """
    yd = _yd()
    date_str = ref_date.strftime("%d-%m-%Y")
    try:
        rate = yd.di_over(date_str)
        if rate is None:
            return None
        r = float(rate)
        if r == 0.0:
            return None
        # Sanity: se r < 0.005 é taxa DIÁRIA → anualizar (base 252)
        if 0 < r < 0.005:
            r = (1 + r) ** 252 - 1
            logger.debug(f"di_over era diária, anualizada: {r:.6f}")
        return r
    except Exception as exc:
        logger.debug(f"di_over({date_str}) sem dado: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# IPCA projetado (fonte: BCB Focus)
# ─────────────────────────────────────────────────────────────────

def fetch_ipca_focus(ref_date: date) -> Optional[float]:
    """
    Retorna IPCA projetado 12m em DECIMAL.
    Ex: 0.045 para projeção de 4,5% a.a.

    Retorna None se indisponível (usa inflação implícita da curva como fallback
    no carrego.py).
    """
    yd = _yd()
    date_str = ref_date.strftime("%d-%m-%Y")
    try:
        rate = yd.ipca.taxa_projetada(date_str)
        if rate is None:
            return None
        r = float(rate)
        # Sanity: se r > 2.0 está em % → dividir por 100
        if r > 2.0:
            r = r / 100.0
        return r if r > 0 else None
    except Exception as exc:
        logger.debug(f"ipca.taxa_projetada({date_str}) sem dado: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# Calendário de dias úteis BR
# ─────────────────────────────────────────────────────────────────

def dias_uteis_br(start: date, end: date) -> list[date]:
    """
    Retorna lista de dias úteis brasileiros entre start e end (inclusive).
    Usa pyield.du.gerar(); fallback para dias da semana se pyield falhar.
    """
    yd = _yd()
    try:
        raw = yd.du.gerar(start, end)
        # raw pode ser polars Series, lista, ou array
        if hasattr(raw, "to_list"):
            dates = raw.to_list()
        else:
            dates = list(raw)
        # garantir tipo date
        return [d.date() if hasattr(d, "date") else d for d in dates]
    except Exception as exc:
        logger.warning(f"du.gerar falhou ({exc}), usando fallback sem feriados.")
        # fallback simples: só exclui sábado e domingo
        out = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out
