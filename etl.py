"""
etl.py — orquestrador do pipeline diário IMA-B 5.

Uso:
    python etl.py               # processa hoje
    python etl.py --backfill    # retroage BACKFILL_DAYS dias úteis
    python etl.py --date 2025-03-14  # data específica (YYYY-MM-DD)

O script é idempotente: re-executar para a mesma data substitui o registro.
Ideal para rodar às ~18h (após fechamento do mercado BR) via:
    - GitHub Actions (ver .github/workflows/daily_update.yml)
    - Windows Task Scheduler
    - cron no Linux/macOS
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from config import BACKFILL_DAYS
from src.collector import fetch_ntnb, fetch_di_over, fetch_ipca_focus, dias_uteis_br
from src.carrego import calcular_carrego
from src.db import init_db, init_db_imab, ja_tem_dado, upsert_carrego, upsert_composicao, get_ultimo_cdi

# Mapeamento índice → tabelas e flag de filtro
_INDEX_CONFIG = {
    "imab5": {
        "carrego_table":    "carrego_historico",
        "composicao_table": "composicao_imab5",
        "filtrar":          True,
        "label":            "IMA-B 5",
    },
    "imab": {
        "carrego_table":    "carrego_historico_imab",
        "composicao_table": "composicao_imab",
        "filtrar":          False,
        "label":            "IMA-B",
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("etl")


# ─────────────────────────────────────────────────────────────────
# Processamento de uma única data
# ─────────────────────────────────────────────────────────────────

def processar_data(ref: date, forcar: bool = False, index: str = "imab5") -> bool:
    """
    Coleta, calcula e persiste o carrego para `ref`.

    Args:
        ref    : data de referência
        forcar : se True, reprocessa mesmo que já exista no banco
        index  : "imab5" (NTN-Bs ≤ 5 anos) ou "imab" (todos os NTN-Bs)

    Returns:
        True se processou com sucesso, False se pulou (sem dado ou já existe).
    """
    cfg = _INDEX_CONFIG[index]
    carrego_table    = cfg["carrego_table"]
    composicao_table = cfg["composicao_table"]
    filtrar          = cfg["filtrar"]

    if not forcar and ja_tem_dado(ref, table=carrego_table):
        logger.debug(f"{ref} [{cfg['label']}]: já existe no banco, pulando.")
        return False

    # 1. NTN-B (ANBIMA via pyield)
    ntnb_df = fetch_ntnb(ref)
    if ntnb_df.empty:
        logger.info(f"{ref}: sem dado NTN-B (feriado ou fim de semana?).")
        return False

    # 2. CDI
    di = fetch_di_over(ref)
    if di is None:
        # fallback: último CDI disponível no banco
        ultimo_cdi_pct = get_ultimo_cdi(table=carrego_table)
        if ultimo_cdi_pct is not None:
            di = ultimo_cdi_pct / 100  # converter % → decimal
            logger.warning(f"{ref}: CDI indisponível, usando último valor {di:.4%}")
        else:
            logger.warning(f"{ref}: CDI sem fallback disponível.")

    # 3. IPCA Focus (opcional — carrego.py usa inflação implícita da curva)
    ipca_focus = fetch_ipca_focus(ref)

    # 4. Calcular carrego
    try:
        snap, bonds_com_pesos = calcular_carrego(
            ntnb_df,
            ref_date=ref,
            ipca_focus=ipca_focus,
            di_annual=di,
            filtrar=filtrar,
        )
    except ValueError as exc:
        logger.error(f"{ref} [{cfg['label']}]: cálculo falhou — {exc}")
        return False

    # 5. Persistir
    upsert_carrego(snap, table=carrego_table)
    upsert_composicao(bonds_com_pesos, ref, table=composicao_table)

    logger.info(
        f"[{cfg['label']}] {ref} | "
        f"IPCA+{snap['ytm_real']:.2f}% real | "
        f"{snap['carrego_nominal']:.2f}% carrego nom. | "
        f"{snap['premio_vs_cdi']:+.2f} pp vs CDI"
        if snap["premio_vs_cdi"] is not None else
        f"[{cfg['label']}] {ref} | IPCA+{snap['ytm_real']:.2f}% real | {snap['carrego_nominal']:.2f}% carrego nom."
    )
    return True


# ─────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────

def run_hoje(index: str = "imab5") -> None:
    """Processa apenas o dia atual."""
    processar_data(date.today(), forcar=True, index=index)


def run_backfill(n_dias: int = BACKFILL_DAYS, index: str = "imab5") -> None:
    """
    Retroage n_dias úteis a partir de ontem.
    Pula datas que já existem no banco (incremental).
    Adiciona um delay de 0.5s entre chamadas para não sobrecarregar a ANBIMA.
    """
    fim = date.today() - timedelta(days=1)
    ini = fim - timedelta(days=n_dias * 2)  # margem para fins de semana/feriados
    datas = dias_uteis_br(ini, fim)
    datas = datas[-n_dias:]  # garante exatamente N dias úteis

    logger.info(
        f"Backfill [{_INDEX_CONFIG[index]['label']}]: "
        f"{len(datas)} dias úteis ({datas[0]} -> {datas[-1]})"
    )

    processados = 0
    for i, d in enumerate(datas, 1):
        ok = processar_data(d, index=index)
        if ok:
            processados += 1
            time.sleep(0.5)  # gentil com a ANBIMA
        if i % 20 == 0:
            logger.info(f"  ... {i}/{len(datas)} datas processadas")

    # Depois do backfill, processar hoje também
    processar_data(date.today(), forcar=True, index=index)
    logger.info(f"Backfill concluído: {processados} novos registros.")


def run_data_especifica(data_str: str, index: str = "imab5") -> None:
    """Processa uma data específica no formato YYYY-MM-DD."""
    try:
        ref = date.fromisoformat(data_str)
    except ValueError:
        logger.error(f"Data inválida: '{data_str}'. Use formato YYYY-MM-DD.")
        sys.exit(1)
    processar_data(ref, forcar=True, index=index)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETL diário do carrego do IMA-B 5 / IMA-B."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--backfill",
        action="store_true",
        help="Retroage N dias úteis (veja --days).",
    )
    group.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Processa uma data específica.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Número de dias úteis a retroagir no --backfill. "
            f"Default: {BACKFILL_DAYS} (config.py). "
            "Ex: --backfill --days 1260 para ~5 anos."
        ),
    )
    parser.add_argument(
        "--index",
        choices=["imab5", "imab"],
        default="imab5",
        help=(
            "Índice a processar: 'imab5' (NTN-Bs <= 5 anos, default) "
            "ou 'imab' (todos os NTN-Bs)."
        ),
    )
    args = parser.parse_args()

    label = _INDEX_CONFIG[args.index]["label"]
    logger.info(f"ETL {label} iniciado")
    init_db()
    init_db_imab()

    if args.backfill:
        n = args.days if args.days is not None else BACKFILL_DAYS
        run_backfill(n_dias=n, index=args.index)
    elif args.date:
        run_data_especifica(args.date, index=args.index)
    else:
        run_hoje(index=args.index)

    logger.info(f"ETL {label} concluido")


if __name__ == "__main__":
    main()
