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
import json
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from threading import Lock
import time
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_LOCK = Lock()
_BINARY_CACHE: dict[str, tuple[float, bytes]] = {}
_FOCUS_CACHE: tuple[float, list[dict]] | None = None
_CURRENT_DATA_TTL_SECONDS = 15 * 60
_FOCUS_TTL_SECONDS = 60 * 60

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
BCB_FOCUS_12M_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoInflacao12Meses"
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

def fetch_ipca_focus(ref_date: date, force_refresh: bool = False) -> Optional[float]:
    """
    Retorna IPCA projetado 12m em DECIMAL.
    Ex: 0.045 para projeção de 4,5% a.a.

    Retorna None se indisponível (usa inflação implícita da curva como fallback
    no carrego.py).
    """
    info = fetch_ipca_focus_info(ref_date, force_refresh=force_refresh)
    return info["valor"] / 100 if info else None


def fetch_ipca_focus_info(ref_date: date, force_refresh: bool = False) -> Optional[dict]:
    """Busca a mediana suavizada do IPCA 12m no Olinda/BCB."""
    try:
        rows = _fetch_focus_12m_rows(force_refresh=force_refresh)
        elegiveis = [row for row in rows if date.fromisoformat(row["Data"]) <= ref_date]
        if not elegiveis:
            return None
        row = max(elegiveis, key=lambda item: item["Data"])
        return {
            "valor": float(row["Mediana"]),
            "data": date.fromisoformat(row["Data"]),
            "fonte": "BCB Focus IPCA 12m suavizado",
        }
    except Exception as exc:
        logger.warning(f"Focus IPCA 12m indisponivel para {ref_date}: {exc}")
        return None


def _fetch_focus_12m_rows(force_refresh: bool = False) -> list[dict]:
    global _FOCUS_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force_refresh
            and _FOCUS_CACHE is not None
            and now - _FOCUS_CACHE[0] < _FOCUS_TTL_SECONDS
        ):
            return _FOCUS_CACHE[1]
    params = {
        "$top": "1000",
        "$format": "json",
        "$filter": (
            "Indicador eq 'IPCA' and Suavizada eq 'S' and baseCalculo eq 0"
        ),
        "$orderby": "Data desc",
    }
    url = BCB_FOCUS_12M_URL + "?" + "&".join(
        "{}={}".format(key, urllib.parse.quote(value, safe="'"))
        for key, value in params.items()
    )
    request = urllib.request.Request(url, headers={"User-Agent": "IMAB-dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.load(response).get("value", [])
    with _CACHE_LOCK:
        _FOCUS_CACHE = (time.monotonic(), rows)
    return rows


def fetch_fiinfra_macro(
    ref_date: date,
    target_duration: Optional[float] = None,
    lookback_days: int = 5,
    force_refresh: bool = False,
) -> dict:
    """Coleta macro no ultimo dia disponivel e casa a NTN-B com a duration alvo."""
    resultado = {
        "data_solicitada": ref_date,
        "ntnb": None,
        "ntnb_vencimento": None,
        "ntnb_duration": None,
        "ntnb_data": None,
        "ntnb_status": "INDISPONIVEL",
        "ntnb_fonte": "ANBIMA taxas indicativas via pyield",
        "cdi": None,
        "cdi_data": None,
        "cdi_status": "INDISPONIVEL",
        "cdi_fonte": "DI Over BCB/B3 via pyield",
        "inflacao_implicita": None,
        "inflacao_data": None,
        "inflacao_status": "INDISPONIVEL",
        "inflacao_fonte": "Breakeven da NTN-B de referencia",
        "ipca_focus": None,
        "ipca_focus_data": None,
        "ipca_focus_status": "INDISPONIVEL",
        "ipca_focus_fonte": "BCB Focus IPCA 12m suavizado",
        "fonte": "ANBIMA + BCB Focus/DI",
    }

    focus = fetch_ipca_focus_info(ref_date, force_refresh=force_refresh)
    if focus:
        resultado.update({
            "ipca_focus": focus["valor"],
            "ipca_focus_data": focus["data"],
            "ipca_focus_status": _freshness_status(
                focus["data"], ref_date, max_business_days=5
            ),
        })

    for data_busca in _lookback_dates(ref_date, lookback_days):
        ntnb_df = fetch_ntnb(data_busca)
        if ntnb_df.empty or "taxa_indicativa" not in ntnb_df:
            continue
        validos = ntnb_df.dropna(subset=["taxa_indicativa"])
        if validos.empty:
            continue
        row = selecionar_ntnb_referencia(validos, target_duration)
        taxa = _percent_or_decimal(row.get("taxa_indicativa"))
        resultado.update({
            "ntnb": taxa * 100 if taxa is not None else None,
            "ntnb_vencimento": row.get("data_vencimento"),
            "ntnb_duration": _percent_or_decimal(row.get("duration"), percent=False),
            "ntnb_data": data_busca,
            "ntnb_status": _freshness_status(
                data_busca, ref_date, max_business_days=1
            ),
        })
        implicita = _percent_or_decimal(row.get("inflacao_implicita"))
        if implicita is not None:
            resultado.update({
                "inflacao_implicita": implicita * 100,
                "inflacao_data": data_busca,
                "inflacao_status": _freshness_status(
                    data_busca, ref_date, max_business_days=1
                ),
            })
        break

    for data_busca in _lookback_dates(ref_date, lookback_days):
        di = fetch_di_over(data_busca)
        if di is not None and pd.notna(di):
            resultado.update({
                "cdi": di * 100,
                "cdi_data": data_busca,
                "cdi_status": _freshness_status(
                    data_busca, ref_date, max_business_days=1
                ),
            })
            break

    return resultado


def selecionar_ntnb_referencia(df: pd.DataFrame, target_duration: Optional[float] = None):
    """Seleciona a NTN-B de duration mais proxima; sem alvo, usa a mediana da curva."""
    if "duration" not in df or not df["duration"].notna().any():
        return df.iloc[-1]
    validos = df.dropna(subset=["duration"]).copy()
    alvo = float(target_duration) if target_duration is not None else float(validos["duration"].median())
    return validos.loc[(validos["duration"].astype(float) - alvo).abs().idxmin()]


def fetch_cotacoes_b3(
    ref_date: date,
    tickers=None,
    url: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """Retorna o ultimo fechamento B3 ate ``ref_date`` para cada ticker."""
    tickers = {str(t).upper() for t in (tickers or FIINFRA_FUNDOS)}
    raw = _download_zip(
        url or B3_COTAHIST_URL.format(year=ref_date.year),
        force_refresh=force_refresh,
    )
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


def fetch_cotas_cvm(
    ref_date: date,
    fundos=None,
    url: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """Retorna a ultima cota patrimonial CVM ate a data por ticker."""
    fundos = fundos or FIINFRA_FUNDOS
    raw = _download_zip(
        url or CVM_INF_DIARIO_URL.format(year=ref_date.year, month=ref_date.month),
        force_refresh=force_refresh,
    )
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


def fetch_fiinfra_fundos(ref_date: date, fundos=None, force_refresh: bool = False) -> list[dict]:
    """Combina fechamentos B3 e cotas patrimoniais CVM."""
    return fetch_fiinfra_fundos_result(
        ref_date, fundos=fundos, force_refresh=force_refresh
    )["fundos"]


def fetch_fiinfra_fundos_result(
    ref_date: date,
    fundos=None,
    force_refresh: bool = False,
) -> dict:
    """Coleta B3 e CVM de forma independente, preservando resultados parciais."""
    fundos = fundos or FIINFRA_FUNDOS
    erros = {}
    try:
        mercado = fetch_cotacoes_b3(
            ref_date, fundos, force_refresh=force_refresh
        )
    except Exception as exc:
        logger.warning("Falha na coleta B3 FI-Infra: %s", exc)
        mercado = {}
        erros["b3"] = str(exc)
    try:
        patrimonial = fetch_cotas_cvm(
            ref_date, fundos, force_refresh=force_refresh
        )
    except Exception as exc:
        logger.warning("Falha na coleta CVM FI-Infra: %s", exc)
        patrimonial = {}
        erros["cvm"] = str(exc)
    rows = [{
        "ticker": ticker,
        "cnpj": cnpj,
        "cota_mercado": mercado.get(ticker, {}).get("valor"),
        "cota_mercado_data": mercado.get(ticker, {}).get("data"),
        "cota_mercado_fonte": mercado.get(ticker, {}).get("fonte"),
        "cota_mercado_status": _freshness_status(
            mercado.get(ticker, {}).get("data"), ref_date, max_business_days=1
        ),
        "cota_patrimonial": patrimonial.get(ticker, {}).get("valor"),
        "cota_patrimonial_data": patrimonial.get(ticker, {}).get("data"),
        "cota_patrimonial_fonte": patrimonial.get(ticker, {}).get("fonte"),
        "cota_patrimonial_status": _freshness_status(
            patrimonial.get(ticker, {}).get("data"), ref_date, max_business_days=2
        ),
    } for ticker, cnpj in fundos.items()]
    return {"fundos": rows, "erros": erros, "data_solicitada": ref_date}


def _download_zip(
    url: str,
    force_refresh: bool = False,
    ttl_seconds: int = _CURRENT_DATA_TTL_SECONDS,
) -> bytes:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _BINARY_CACHE.get(url)
        if not force_refresh and cached and now - cached[0] < ttl_seconds:
            return cached[1]
    request = urllib.request.Request(url, headers={"User-Agent": "IMAB-dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    with _CACHE_LOCK:
        _BINARY_CACHE[url] = (time.monotonic(), raw)
        if len(_BINARY_CACHE) > 8:
            oldest = min(_BINARY_CACHE, key=lambda key: _BINARY_CACHE[key][0])
            _BINARY_CACHE.pop(oldest, None)
    return raw


def clear_collector_cache() -> None:
    """Limpa caches em memoria; util para refresh explicito e testes."""
    global _FOCUS_CACHE
    with _CACHE_LOCK:
        _BINARY_CACHE.clear()
        _FOCUS_CACHE = None


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


def _lookback_dates(ref_date: date, limit: int):
    current = ref_date
    emitted = 0
    while emitted <= limit:
        if current.weekday() < 5:
            yield current
            emitted += 1
        current -= timedelta(days=1)


def _freshness_status(
    data_value: Optional[date],
    ref_date: date,
    max_business_days: int = 0,
) -> str:
    if data_value is None:
        return "INDISPONIVEL"
    if data_value > ref_date:
        return "DATA_FUTURA"
    gap = _business_days_gap(data_value, ref_date)
    if gap == 0:
        return "ATUALIZADO"
    if gap <= max_business_days:
        return "DENTRO_SLA"
    return "DEFASADO"


def _business_days_gap(start: date, end: date) -> int:
    """Conta dias de semana em (start, end], sem calendário de feriados."""
    if start >= end:
        return 0
    current = start + timedelta(days=1)
    total = 0
    while current <= end:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


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
