"""
src/db.py — camada de persistência SQLite.

Convenção de unidades no banco:
  • Taxas em % a.a.  (ex: 6.15, não 0.0615)
  • Datas como TEXT  "YYYY-MM-DD"
  • Prêmio em p.p.   (ex: +0.28)

Isso mantém o banco legível diretamente via DB browser ou SQL.

Tabelas IMA-B 5 : carrego_historico   + composicao_imab5
Tabelas IMA-B   : carrego_historico_imab + composicao_imab
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Generator, Optional

import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)

# ── Schema DDL — IMA-B 5 (tabelas originais) ───────────────────
_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS carrego_historico (
    data             TEXT PRIMARY KEY,   -- YYYY-MM-DD
    ytm_real         REAL,               -- % a.a. (taxa real pond.)
    ipca_proj        REAL,               -- % a.a. (IPCA projetado)
    carrego_nominal  REAL,               -- % a.a. (carrego bruto)
    carrego_diario   REAL,               -- % a.d.
    cdi_anual        REAL,               -- % a.a.
    premio_vs_cdi    REAL,               -- p.p. (carrego − CDI)
    n_bonds          INTEGER,
    metodo_peso      TEXT,
    fonte_ipca       TEXT,
    atualizado_em    TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS composicao_imab5 (
    data              TEXT,              -- YYYY-MM-DD
    data_vencimento   TEXT,              -- YYYY-MM-DD
    taxa_indicativa   REAL,              -- % a.a.
    peso              REAL,              -- fracao [0..1]
    duration          REAL,              -- anos
    inflacao_implicita REAL,             -- % a.a.
    PRIMARY KEY (data, data_vencimento)
);

CREATE INDEX IF NOT EXISTS idx_carrego_data ON carrego_historico(data);
CREATE INDEX IF NOT EXISTS idx_composicao_data ON composicao_imab5(data);
"""

# ── Schema DDL — IMA-B (tabelas novas, mesma estrutura) ────────
_SCHEMA_IMAB = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS carrego_historico_imab (
    data             TEXT PRIMARY KEY,
    ytm_real         REAL,
    ipca_proj        REAL,
    carrego_nominal  REAL,
    carrego_diario   REAL,
    cdi_anual        REAL,
    premio_vs_cdi    REAL,
    n_bonds          INTEGER,
    metodo_peso      TEXT,
    fonte_ipca       TEXT,
    atualizado_em    TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS composicao_imab (
    data              TEXT,
    data_vencimento   TEXT,
    taxa_indicativa   REAL,
    peso              REAL,
    duration          REAL,
    inflacao_implicita REAL,
    PRIMARY KEY (data, data_vencimento)
);

CREATE INDEX IF NOT EXISTS idx_carrego_imab_data ON carrego_historico_imab(data);
CREATE INDEX IF NOT EXISTS idx_composicao_imab_data ON composicao_imab(data);
"""


# ─────────────────────────────────────────────────────────────────
# Conexão
# ─────────────────────────────────────────────────────────────────

@contextmanager
def _conn(db_path=None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None) -> None:
    """Cria as tabelas IMA-B 5 se não existirem. Idempotente."""
    with _conn(db_path) as conn:
        conn.executescript(_SCHEMA)
    logger.info(f"Banco IMA-B 5 inicializado em {db_path or DB_PATH}")


def init_db_imab(db_path=None) -> None:
    """Cria as tabelas IMA-B (full) se não existirem. Idempotente."""
    with _conn(db_path) as conn:
        conn.executescript(_SCHEMA_IMAB)
    logger.info(f"Banco IMA-B inicializado em {db_path or DB_PATH}")


# ─────────────────────────────────────────────────────────────────
# Escrita
# ─────────────────────────────────────────────────────────────────

def upsert_carrego(snap: dict, db_path=None, table: str = "carrego_historico") -> None:
    """Grava (ou substitui) um snapshot diário de carrego."""
    row = {**snap, "data": str(snap["data"])}
    with _conn(db_path) as conn:
        conn.execute(f"""
            INSERT OR REPLACE INTO {table}
              (data, ytm_real, ipca_proj, carrego_nominal, carrego_diario,
               cdi_anual, premio_vs_cdi, n_bonds, metodo_peso, fonte_ipca)
            VALUES
              (:data, :ytm_real, :ipca_proj, :carrego_nominal, :carrego_diario,
               :cdi_anual, :premio_vs_cdi, :n_bonds, :metodo_peso, :fonte_ipca)
        """, row)


def upsert_composicao(
    bonds: pd.DataFrame,
    ref_date: date,
    db_path=None,
    table: str = "composicao_imab5",
) -> None:
    """
    Grava a composição do índice proxy para ref_date.
    bonds deve ter as colunas adicionadas por carrego.calcular_pesos()
    e taxa_indicativa já em DECIMAL.
    """
    date_str = str(ref_date)
    with _conn(db_path) as conn:
        conn.execute(f"DELETE FROM {table} WHERE data = ?", (date_str,))
        for _, r in bonds.iterrows():
            taxa = r.get("taxa_indicativa", None)
            inflacao = r.get("inflacao_implicita", None)
            conn.execute(f"""
                INSERT INTO {table}
                  (data, data_vencimento, taxa_indicativa, peso, duration, inflacao_implicita)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                str(r["data_vencimento"])[:10],
                round(taxa * 100, 4) if taxa is not None else None,
                round(float(r.get("peso", 0)), 6),
                round(float(r.get("duration", 0)), 4) if r.get("duration") is not None else None,
                round(inflacao * 100, 4) if inflacao is not None else None,
            ))


# ─────────────────────────────────────────────────────────────────
# Leitura
# ─────────────────────────────────────────────────────────────────

def ja_tem_dado(ref_date: date, db_path=None, table: str = "carrego_historico") -> bool:
    """Verifica se já existe snapshot para essa data."""
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE data = ?",
            (str(ref_date),)
        ).fetchone()
    return row is not None


def get_ultimo_carrego(db_path=None, table: str = "carrego_historico") -> Optional[dict]:
    """Retorna o snapshot mais recente ou None se banco vazio."""
    with _conn(db_path) as conn:
        cursor = conn.execute(
            f"SELECT * FROM {table} ORDER BY data DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))


def get_ultimo_cdi(db_path=None, table: str = "carrego_historico") -> Optional[float]:
    """Retorna o último CDI disponível no banco (% a.a.)."""
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT cdi_anual FROM {table} "
            "WHERE cdi_anual IS NOT NULL ORDER BY data DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None


def load_carrego_historico(
    days: int = 252 * 3,
    db_path=None,
    table: str = "carrego_historico",
) -> pd.DataFrame:
    """Carrega os N dias mais recentes de histórico, ordenado por data ASC."""
    with _conn(db_path) as conn:
        df = pd.read_sql(
            f"SELECT * FROM (SELECT * FROM {table} ORDER BY data DESC LIMIT ?) t ORDER BY data ASC",
            conn,
            params=(days,),
            parse_dates=["data"],
        )
    return df


def load_composicao(
    ref_date: Optional[date] = None,
    db_path=None,
    table: str = "composicao_imab5",
) -> pd.DataFrame:
    """Carrega composição do índice para ref_date (default: mais recente)."""
    date_str = str(ref_date) if ref_date else None
    with _conn(db_path) as conn:
        if date_str:
            df = pd.read_sql(
                f"SELECT * FROM {table} WHERE data = ? ORDER BY data_vencimento",
                conn,
                params=(date_str,),
            )
        else:
            latest = conn.execute(
                f"SELECT MAX(data) FROM {table}"
            ).fetchone()[0]
            if not latest:
                return pd.DataFrame()
            df = pd.read_sql(
                f"SELECT * FROM {table} WHERE data = ? ORDER BY data_vencimento",
                conn,
                params=(latest,),
            )
    return df
