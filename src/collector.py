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
import io
import re
import urllib.request
import zipfile
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

FIINFRA_FUNDOS = {
    "IFRA11": "34.633.510/0001-18",
    "BDIF11": "40.502.607/0001-94",
    "KDIF11": "26.324.298/0001-89",
    "JURO11": "42.730.834/0001-00",
}

B3_COTAHIST_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP"
CVM_INF_DIARIO_URL = (
    "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/"
    "inf_diario_fi_{year}{month:02d}.zip"
)


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


def fetch_fiinfra_macro(ref_date: date) -> dict:
    """Coleta os dados macro usados pela regua, com data e fonte."""
    ntnb_df = fetch_ntnb(ref_date)
    ntnb = inflacao = duration = None
    if not ntnb_df.empty and "taxa_indicativa" in ntnb_df:
        validos = ntnb_df.dropna(subset=["taxa_indicativa"])
        if not validos.empty:
            if "duration" in validos and validos["duration"].notna().any():
                row = validos.loc[validos["duration"].idxmax()]
                duration = _percent_or_decimal(row.get("duration"), percent=False)
            else:
                row = validos.iloc[-1]
            ntnb_decimal = _percent_or_decimal(row.get("taxa_indicativa"))
            ntnb = ntnb_decimal * 100 if ntnb_decimal is not None else None
            implicita_decimal = _percent_or_decimal(row.get("inflacao_implicita"))
            inflacao = implicita_decimal * 100 if implicita_decimal is not None else None

    di = fetch_di_over(ref_date)
    ipca = fetch_ipca_focus(ref_date)
    if inflacao is None and ipca is not None:
        inflacao = ipca * 100
    return {
        "data": ref_date,
        "ntnb": ntnb,
        "ntnb_duration": duration,
        "cdi": di * 100 if di is not None else None,
        "inflacao_implicita": inflacao,
        "fonte": "ANBIMA/BCB via pyield",
    }


def fetch_cotacoes_b3(ref_date: date, tickers=None, url: Optional[str] = None) -> dict:
    """Retorna o ultimo fechamento B3 ate ``ref_date`` para cada ticker."""
    tickers = {str(t).upper() for t in (tickers or FIINFRA_FUNDOS)}
    raw = _download_zip(url or B3_COTAHIST_URL.format(year=ref_date.year))
    encontrados = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        member = next(n for n in archive.namelist() if n.upper().endswith(".TXT"))
        with archive.open(member) as stream:
            for raw_line in stream:
                line = raw_line.decode("latin-1")
                if line[:2] != "01":
                    continue
                ticker = line[12:24].strip().upper()
                if ticker not in tickers:
                    continue
                try:
                    data_pregao = date.fromisoformat(
                        f"{line[2:6]}-{line[6:8]}-{line[8:10]}"
                    )
                    preco = int(line[108:121]) / 100
                except (ValueError, IndexError):
                    continue
                if data_pregao <= ref_date and (
                    ticker not in encontrados or data_pregao > encontrados[ticker]["data"]
                ):
                    encontrados[ticker] = {
                        "valor": preco,
                        "data": data_pregao,
                        "fonte": "B3 COTAHIST",
                    }
    return encontrados


def fetch_cotas_cvm(ref_date: date, fundos=None, url: Optional[str] = None) -> dict:
    """Retorna a ultima cota patrimonial CVM ate a data por ticker."""
    fundos = fundos or FIINFRA_FUNDOS
    raw = _download_zip(url or CVM_INF_DIARIO_URL.format(
        year=ref_date.year, month=ref_date.month
    ))
    frames = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.namelist():
            if member.lower().endswith(".csv"):
                with archive.open(member) as stream:
                    frames.append(pd.read_csv(stream, sep=";", encoding="latin-1", dtype=str))
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    cnpj_col = _first_column(df, "CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO")
    data_col = _first_column(df, "DT_COMPTC", "Data_Competencia")
    cota_col = _first_column(df, "VL_QUOTA", "VL_COTA", "Valor_Cota")
    if not all((cnpj_col, data_col, cota_col)):
        return {}
    df["_cnpj"] = df[cnpj_col].map(_only_digits)
    df["_data"] = pd.to_datetime(df[data_col], errors="coerce").dt.date
    df["_cota"] = df[cota_col].map(_parse_decimal)
    resultado = {}
    for ticker, cnpj in fundos.items():
        rows = df[(df["_cnpj"] == _only_digits(cnpj)) & (df["_data"] <= ref_date)]
        rows = rows.dropna(subset=["_data", "_cota"]).sort_values("_data")
        if not rows.empty:
            row = rows.iloc[-1]
            resultado[ticker] = {
                "valor": float(row["_cota"]),
                "data": row["_data"],
                "fonte": "CVM Informe Diario",
            }
    return resultado


def fetch_fiinfra_fundos(ref_date: date, fundos=None) -> list[dict]:
    """Combina fechamentos B3 e cotas patrimoniais CVM."""
    fundos = fundos or FIINFRA_FUNDOS
    mercado = fetch_cotacoes_b3(ref_date, fundos)
    patrimonial = fetch_cotas_cvm(ref_date, fundos)
    return [{
        "ticker": ticker,
        "cnpj": cnpj,
        "cota_mercado": mercado.get(ticker, {}).get("valor"),
        "cota_mercado_data": mercado.get(ticker, {}).get("data"),
        "cota_mercado_fonte": mercado.get(ticker, {}).get("fonte"),
        "cota_patrimonial": patrimonial.get(ticker, {}).get("valor"),
        "cota_patrimonial_data": patrimonial.get(ticker, {}).get("data"),
        "cota_patrimonial_fonte": patrimonial.get(ticker, {}).get("fonte"),
    } for ticker, cnpj in fundos.items()]


@lru_cache(maxsize=8)
def _download_zip(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "IMAB-dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _only_digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _parse_decimal(value) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _first_column(df: pd.DataFrame, *candidates) -> Optional[str]:
    return next((col for col in candidates if col in df.columns), None)


def _percent_or_decimal(value, percent: bool = True) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not percent:
        return parsed
    return parsed / 100 if abs(parsed) > 1 else parsed


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
