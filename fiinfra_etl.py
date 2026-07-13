"""Coleta diaria de observacoes brutas da cesta FI-Infra.

Este processo nao gera recomendacao, nao aplica premissas manuais e nao salva
snapshot decisorio. Ele apenas preserva os dados de mercado e macro que serao
necessarios para validar a regua ao longo do tempo.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from src.collector import fetch_fiinfra_fundos_result, fetch_fiinfra_macro
from src.db import init_db_fiinfra, load_fiinfra_fundos, save_fiinfra_collection_observation


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fiinfra_etl")


def duration_alvo() -> float:
    """Usa a duration mediana previamente confirmada; sem historico, 8 anos."""
    fundos = load_fiinfra_fundos()
    if fundos.empty or "duration" not in fundos:
        return 8.0
    values = pd.to_numeric(fundos["duration"], errors="coerce").dropna()
    values = values[values > 0]
    return float(values.median()) if not values.empty else 8.0


def coletar_observacoes(ref_date: date, force_refresh: bool = True) -> dict:
    """Busca fontes independentes e sempre registra o resultado parcial."""
    batch = {
        "collection_id": uuid4().hex,
        "data_solicitada": ref_date,
        "coletado_em": datetime.now().isoformat(timespec="seconds"),
        "macro": {},
        "fundos": [],
        "fontes_tentadas": {},
        "erros": [],
    }
    try:
        batch["macro"] = fetch_fiinfra_macro(
            ref_date,
            target_duration=duration_alvo(),
            force_refresh=force_refresh,
        )
    except Exception as exc:  # preserva B3/CVM quando macro falha
        logger.warning("Falha na coleta macro FI-Infra: %s", exc)
        batch["erros"].append(f"macro: {exc}")

    try:
        fundos_result = fetch_fiinfra_fundos_result(ref_date, force_refresh=force_refresh)
        batch["fundos"] = fundos_result.get("fundos", [])
        batch["fontes_tentadas"] = fundos_result.get("fontes_tentadas", {})
        batch["erros"].extend(
            f"{fonte}: {erro}" for fonte, erro in fundos_result.get("erros", {}).items()
        )
    except Exception as exc:  # ainda registra macro e a falha de fundos
        logger.warning("Falha na coleta de fundos FI-Infra: %s", exc)
        batch["erros"].append(f"fundos: {exc}")

    save_fiinfra_collection_observation(batch)
    logger.info(
        "FI-Infra %s | fundos=%s | erros=%s | collection=%s",
        ref_date,
        len(batch["fundos"]),
        len(batch["erros"]),
        batch["collection_id"][:8],
    )
    return batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Coleta diaria de observacoes FI-Infra.")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Data de referencia; padrao: hoje.")
    args = parser.parse_args()
    ref_date = date.fromisoformat(args.date) if args.date else date.today()
    init_db_fiinfra()
    coletar_observacoes(ref_date)


if __name__ == "__main__":
    main()
