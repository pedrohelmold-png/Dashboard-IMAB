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
import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Generator, Optional

import pandas as pd

from config import DB_PATH
from src.regua_fiinfra import DEFAULT_THRESHOLDS, validar_thresholds

logger = logging.getLogger(__name__)

# ── Schema DDL — IMA-B 5 (tabelas originais) ───────────────────
_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS carrego_historico (
    data             TEXT PRIMARY KEY,   -- YYYY-MM-DD
    ytm_real         REAL,               -- % a.a. (taxa real pond.)
    ipca_proj        REAL,               -- % a.a. (IPCA projetado)
    ipca_focus       REAL,               -- % a.a. (Focus 12m)
    ipca_implicita   REAL,               -- % a.a. (breakeven ponderado)
    ipca_focus_data  TEXT,
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
    ipca_focus       REAL,
    ipca_implicita   REAL,
    ipca_focus_data  TEXT,
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

# Schema DDL -- Regua de Ciclo FI-Infra
_SCHEMA_FIINFRA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS fiinfra_thresholds (
    chave          TEXT PRIMARY KEY,
    valor          REAL NOT NULL,
    atualizado_em  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS fiinfra_snapshots (
    data                      TEXT PRIMARY KEY,
    collection_id             TEXT,
    data_solicitada           TEXT,
    metodologia_version       TEXT,
    cobertura_fundos          INTEGER,
    juro_real_caro_ref        REAL,
    juro_real_barato_ref      REAL,
    spread_caro_ref           REAL,
    spread_barato_ref         REAL,
    excesso_caro_ref          REAL,
    excesso_barato_ref        REAL,
    ntnb                      REAL,
    ntnb_original             REAL,
    ntnb_fonte                TEXT,
    ntnb_override             INTEGER DEFAULT 0,
    spread                    REAL,
    excesso_mediano           REAL,
    duration_mediana          REAL,
    zona                      TEXT,
    juro_estado               TEXT,
    spread_estado             TEXT,
    excesso_estado            TEXT,
    juro_pos                  REAL,
    spread_pos                REAL,
    excesso_pos               REAL,
    mandato                   TEXT,
    cdi                       REAL,
    cdi_original              REAL,
    cdi_fonte                 TEXT,
    cdi_override              INTEGER DEFAULT 0,
    aliquota                  REAL,
    inflacao_implicita        REAL,
    inflacao_original         REAL,
    inflacao_fonte            TEXT,
    inflacao_override         INTEGER DEFAULT 0,
    ipca_focus                REAL,
    ipca_focus_original       REAL,
    ipca_focus_fonte          TEXT,
    ipca_focus_override       INTEGER DEFAULT 0,
    ipca_focus_data           TEXT,
    ipca_focus_status         TEXT,
    inflacao_usada            REAL,
    inflacao_usada_fonte      TEXT,
    alternativa_liquida_real  REAL,
    yield_fundo_real          REAL,
    acao                      TEXT,
    destino                   TEXT,
    venda_bloqueada           INTEGER DEFAULT 0,
    observacao                TEXT,
    ntnb_vencimento           TEXT,
    ntnb_duration_ref         REAL,
    ntnb_data                 TEXT,
    ntnb_status               TEXT,
    cdi_data                  TEXT,
    cdi_status                TEXT,
    inflacao_data             TEXT,
    inflacao_status           TEXT,
    coletado_em               TEXT,
    atualizado_em             TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS fiinfra_fundos_snapshot (
    data                 TEXT,
    ticker               TEXT,
    cnpj                 TEXT,
    cota_mercado         REAL,
    cota_mercado_original REAL,
    cota_mercado_data    TEXT,
    cota_mercado_fonte   TEXT,
    cota_mercado_status  TEXT,
    cota_mercado_override INTEGER DEFAULT 0,
    cota_patrimonial     REAL,
    cota_patrimonial_original REAL,
    cota_patrimonial_data TEXT,
    cota_patrimonial_fonte TEXT,
    cota_patrimonial_status TEXT,
    cota_patrimonial_override INTEGER DEFAULT 0,
    taxa_total_aa        REAL,
    taxa_total_status    TEXT,
    duration             REAL,
    duration_status      TEXT,
    desconto_observado   REAL,
    desconto_justo       REAL,
    excesso_desconto     REAL,
    elegivel             INTEGER DEFAULT 1,
    motivo_exclusao      TEXT,
    PRIMARY KEY (data, ticker)
);

CREATE TABLE IF NOT EXISTS fiinfra_tranches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,
    data        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    qtd         REAL NOT NULL,
    preco       REAL NOT NULL,
    observacao  TEXT,
    criado_em   TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS fiinfra_snapshot_revisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    data           TEXT NOT NULL,
    revisao_num    INTEGER NOT NULL,
    snapshot_json  TEXT NOT NULL,
    fundos_json    TEXT NOT NULL,
    substituido_em TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_fiinfra_snapshots_data ON fiinfra_snapshots(data);
CREATE INDEX IF NOT EXISTS idx_fiinfra_fundos_data ON fiinfra_fundos_snapshot(data);
CREATE INDEX IF NOT EXISTS idx_fiinfra_tranches_data ON fiinfra_tranches(data);
CREATE INDEX IF NOT EXISTS idx_fiinfra_revisions_data ON fiinfra_snapshot_revisions(data);
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
        _ensure_columns(conn, "carrego_historico", {
            "ipca_focus": "REAL", "ipca_implicita": "REAL", "ipca_focus_data": "TEXT",
        })
    logger.info(f"Banco IMA-B 5 inicializado em {db_path or DB_PATH}")


def init_db_imab(db_path=None) -> None:
    """Cria as tabelas IMA-B (full) se não existirem. Idempotente."""
    with _conn(db_path) as conn:
        conn.executescript(_SCHEMA_IMAB)
        _ensure_columns(conn, "carrego_historico_imab", {
            "ipca_focus": "REAL", "ipca_implicita": "REAL", "ipca_focus_data": "TEXT",
        })
    logger.info(f"Banco IMA-B inicializado em {db_path or DB_PATH}")


def init_db_fiinfra(db_path=None) -> None:
    """Cria as tabelas da Regua FI-Infra se nao existirem. Idempotente."""
    with _conn(db_path) as conn:
        conn.executescript(_SCHEMA_FIINFRA)
        _ensure_columns(conn, "fiinfra_snapshots", {
            "ntnb_vencimento": "TEXT", "ntnb_duration_ref": "REAL",
            "ntnb_data": "TEXT", "ntnb_status": "TEXT", "cdi_data": "TEXT",
            "cdi_status": "TEXT", "inflacao_data": "TEXT",
            "inflacao_status": "TEXT", "coletado_em": "TEXT",
            "ipca_focus": "REAL", "ipca_focus_data": "TEXT",
            "ipca_focus_status": "TEXT", "inflacao_usada": "REAL",
            "inflacao_usada_fonte": "TEXT",
            "collection_id": "TEXT", "data_solicitada": "TEXT",
            "ntnb_original": "REAL", "ntnb_fonte": "TEXT",
            "ntnb_override": "INTEGER DEFAULT 0", "cdi_original": "REAL",
            "cdi_fonte": "TEXT", "cdi_override": "INTEGER DEFAULT 0",
            "inflacao_original": "REAL", "inflacao_fonte": "TEXT",
            "inflacao_override": "INTEGER DEFAULT 0",
            "ipca_focus_original": "REAL", "ipca_focus_fonte": "TEXT",
            "ipca_focus_override": "INTEGER DEFAULT 0",
            "metodologia_version": "TEXT", "cobertura_fundos": "INTEGER",
            "juro_real_caro_ref": "REAL", "juro_real_barato_ref": "REAL",
            "spread_caro_ref": "REAL", "spread_barato_ref": "REAL",
            "excesso_caro_ref": "REAL", "excesso_barato_ref": "REAL",
        })
        _ensure_columns(conn, "fiinfra_fundos_snapshot", {
            "cnpj": "TEXT", "cota_mercado_original": "REAL",
            "cota_mercado_data": "TEXT", "cota_mercado_fonte": "TEXT",
            "cota_mercado_status": "TEXT", "cota_mercado_override": "INTEGER DEFAULT 0",
            "cota_patrimonial_original": "REAL", "cota_patrimonial_data": "TEXT",
            "cota_patrimonial_fonte": "TEXT", "cota_patrimonial_status": "TEXT",
            "cota_patrimonial_override": "INTEGER DEFAULT 0",
            "taxa_total_status": "TEXT", "duration_status": "TEXT",
            "elegivel": "INTEGER DEFAULT 1", "motivo_exclusao": "TEXT",
        })
        for chave, valor in DEFAULT_THRESHOLDS.items():
            conn.execute(
                "INSERT OR IGNORE INTO fiinfra_thresholds (chave, valor) VALUES (?, ?)",
                (chave, valor),
            )
    logger.info(f"Banco FI-Infra inicializado em {db_path or DB_PATH}")


# ─────────────────────────────────────────────────────────────────
# Escrita
# ─────────────────────────────────────────────────────────────────

def upsert_carrego(snap: dict, db_path=None, table: str = "carrego_historico") -> None:
    """Grava (ou substitui) um snapshot diário de carrego."""
    row = {**snap, "data": str(snap["data"])}
    row.setdefault("ipca_focus", None)
    row.setdefault("ipca_implicita", None)
    row.setdefault("ipca_focus_data", None)
    if row["ipca_focus_data"] is not None:
        row["ipca_focus_data"] = str(row["ipca_focus_data"])
    with _conn(db_path) as conn:
        conn.execute(f"""
            INSERT OR REPLACE INTO {table}
              (data, ytm_real, ipca_proj, ipca_focus, ipca_implicita, ipca_focus_data,
               carrego_nominal, carrego_diario,
               cdi_anual, premio_vs_cdi, n_bonds, metodo_peso, fonte_ipca)
            VALUES
              (:data, :ytm_real, :ipca_proj, :ipca_focus, :ipca_implicita, :ipca_focus_data,
               :carrego_nominal, :carrego_diario,
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


def save_fiinfra_thresholds(thresholds: dict, db_path=None) -> None:
    """Persiste os limiares editaveis da Regua FI-Infra."""
    thresholds = validar_thresholds(thresholds)
    with _conn(db_path) as conn:
        for chave, valor in thresholds.items():
            conn.execute("""
                INSERT INTO fiinfra_thresholds (chave, valor, atualizado_em)
                VALUES (?, ?, datetime('now', 'localtime'))
                ON CONFLICT(chave) DO UPDATE SET
                    valor = excluded.valor,
                    atualizado_em = excluded.atualizado_em
            """, (chave, float(valor)))


def upsert_fiinfra_snapshot(
    snapshot: dict,
    fundos: list[dict],
    db_path=None,
) -> None:
    """Grava a foto consolidada da regua e os dados por fundo."""
    row = {**snapshot, "data": str(snapshot["data"])}
    row["venda_bloqueada"] = int(bool(row.get("venda_bloqueada", False)))
    for key in (
        "ntnb_vencimento", "ntnb_duration_ref", "ntnb_data", "ntnb_status",
        "cdi_data", "cdi_status", "inflacao_data", "inflacao_status", "coletado_em",
        "ipca_focus", "ipca_focus_data", "ipca_focus_status",
        "inflacao_usada", "inflacao_usada_fonte",
        "collection_id", "data_solicitada", "ntnb_original", "ntnb_fonte",
        "ntnb_override", "cdi_original", "cdi_fonte", "cdi_override",
        "inflacao_original", "inflacao_fonte", "inflacao_override",
        "ipca_focus_original", "ipca_focus_fonte", "ipca_focus_override",
        "metodologia_version", "cobertura_fundos", "juro_real_caro_ref",
        "juro_real_barato_ref", "spread_caro_ref", "spread_barato_ref",
        "excesso_caro_ref", "excesso_barato_ref",
    ):
        row.setdefault(key, None)
    for key in (
        "ntnb_vencimento", "ntnb_data", "cdi_data", "inflacao_data", "coletado_em",
        "ipca_focus_data",
        "data_solicitada",
    ):
        if row[key] is not None:
            row[key] = str(row[key])
    for key in ("ntnb_override", "cdi_override", "inflacao_override", "ipca_focus_override"):
        row[key] = int(bool(row[key]))

    with _conn(db_path) as conn:
        _archive_fiinfra_revision(conn, row["data"])
        conn.execute("""
            INSERT OR REPLACE INTO fiinfra_snapshots
              (data, collection_id, data_solicitada, metodologia_version,
               cobertura_fundos, juro_real_caro_ref, juro_real_barato_ref,
               spread_caro_ref, spread_barato_ref, excesso_caro_ref, excesso_barato_ref,
               ntnb, ntnb_original, ntnb_fonte, ntnb_override,
               spread, excesso_mediano, duration_mediana, zona,
               juro_estado, spread_estado, excesso_estado,
               juro_pos, spread_pos, excesso_pos, mandato,
               cdi, cdi_original, cdi_fonte, cdi_override, aliquota,
               inflacao_implicita, inflacao_original, inflacao_fonte, inflacao_override,
               ipca_focus, ipca_focus_original, ipca_focus_fonte, ipca_focus_override,
               ipca_focus_data, ipca_focus_status,
               inflacao_usada, inflacao_usada_fonte,
               alternativa_liquida_real, yield_fundo_real,
               acao, destino, venda_bloqueada, observacao,
               ntnb_vencimento, ntnb_duration_ref, ntnb_data, ntnb_status,
               cdi_data, cdi_status, inflacao_data, inflacao_status, coletado_em)
            VALUES
              (:data, :collection_id, :data_solicitada, :metodologia_version,
               :cobertura_fundos, :juro_real_caro_ref, :juro_real_barato_ref,
               :spread_caro_ref, :spread_barato_ref, :excesso_caro_ref, :excesso_barato_ref,
               :ntnb, :ntnb_original, :ntnb_fonte, :ntnb_override,
               :spread, :excesso_mediano, :duration_mediana, :zona,
               :juro_estado, :spread_estado, :excesso_estado,
               :juro_pos, :spread_pos, :excesso_pos, :mandato,
               :cdi, :cdi_original, :cdi_fonte, :cdi_override, :aliquota,
               :inflacao_implicita, :inflacao_original, :inflacao_fonte, :inflacao_override,
               :ipca_focus, :ipca_focus_original, :ipca_focus_fonte, :ipca_focus_override,
               :ipca_focus_data, :ipca_focus_status,
               :inflacao_usada, :inflacao_usada_fonte,
               :alternativa_liquida_real, :yield_fundo_real,
               :acao, :destino, :venda_bloqueada, :observacao,
               :ntnb_vencimento, :ntnb_duration_ref, :ntnb_data, :ntnb_status,
               :cdi_data, :cdi_status, :inflacao_data, :inflacao_status, :coletado_em)
        """, row)

        conn.execute("DELETE FROM fiinfra_fundos_snapshot WHERE data = ?", (row["data"],))
        for fundo in fundos:
            fundo_row = {**fundo, "data": row["data"]}
            for key in (
                "cnpj", "cota_mercado_original", "cota_mercado_data",
                "cota_mercado_fonte", "cota_mercado_status", "cota_mercado_override",
                "cota_patrimonial_original", "cota_patrimonial_data",
                "cota_patrimonial_fonte", "cota_patrimonial_status",
                "cota_patrimonial_override", "taxa_total_status", "duration_status",
                "elegivel", "motivo_exclusao",
            ):
                fundo_row.setdefault(key, None)
            fundo_row["cota_mercado_override"] = int(bool(fundo_row["cota_mercado_override"]))
            fundo_row["cota_patrimonial_override"] = int(bool(fundo_row["cota_patrimonial_override"]))
            fundo_row["elegivel"] = int(fundo_row["elegivel"] is not False)
            for key in ("cota_mercado_data", "cota_patrimonial_data"):
                if fundo_row[key] is not None:
                    fundo_row[key] = str(fundo_row[key])
            conn.execute("""
                INSERT OR REPLACE INTO fiinfra_fundos_snapshot
                  (data, ticker, cnpj,
                   cota_mercado, cota_mercado_original, cota_mercado_data,
                   cota_mercado_fonte, cota_mercado_status, cota_mercado_override,
                   cota_patrimonial, cota_patrimonial_original, cota_patrimonial_data,
                   cota_patrimonial_fonte, cota_patrimonial_status, cota_patrimonial_override,
                   taxa_total_aa, taxa_total_status, duration, duration_status,
                   desconto_observado, desconto_justo, excesso_desconto,
                   elegivel, motivo_exclusao)
                VALUES
                  (:data, :ticker, :cnpj,
                   :cota_mercado, :cota_mercado_original, :cota_mercado_data,
                   :cota_mercado_fonte, :cota_mercado_status, :cota_mercado_override,
                   :cota_patrimonial, :cota_patrimonial_original, :cota_patrimonial_data,
                   :cota_patrimonial_fonte, :cota_patrimonial_status, :cota_patrimonial_override,
                   :taxa_total_aa, :taxa_total_status, :duration, :duration_status,
                   :desconto_observado, :desconto_justo, :excesso_desconto,
                   :elegivel, :motivo_exclusao)
            """, fundo_row)


def insert_fiinfra_tranche(tranche: dict, db_path=None) -> None:
    """Registra uma tranche executada ou planejada."""
    row = {**tranche, "data": str(tranche["data"])}
    with _conn(db_path) as conn:
        conn.execute("""
            INSERT INTO fiinfra_tranches (tipo, data, ticker, qtd, preco, observacao)
            VALUES (:tipo, :data, :ticker, :qtd, :preco, :observacao)
        """, row)


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


def load_fiinfra_thresholds(db_path=None) -> dict:
    """Carrega limiares da Regua FI-Infra, usando defaults quando faltar algo."""
    thresholds = dict(DEFAULT_THRESHOLDS)
    with _conn(db_path) as conn:
        rows = conn.execute("SELECT chave, valor FROM fiinfra_thresholds").fetchall()
    thresholds.update({chave: valor for chave, valor in rows})
    return thresholds


def get_ultimo_fiinfra_snapshot(db_path=None) -> Optional[dict]:
    """Retorna a foto mais recente da Regua FI-Infra."""
    with _conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM fiinfra_snapshots ORDER BY data DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))


def get_fiinfra_snapshot(ref_date: date, db_path=None) -> Optional[dict]:
    """Retorna o snapshot de uma data especifica, quando existir."""
    with _conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM fiinfra_snapshots WHERE data = ?", (str(ref_date),)
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))


def load_fiinfra_revisions(ref_date: date, db_path=None) -> pd.DataFrame:
    """Carrega revisoes arquivadas para uma data, da mais nova para a mais antiga."""
    with _conn(db_path) as conn:
        df = pd.read_sql(
            """
            SELECT id, data, revisao_num, snapshot_json, fundos_json, substituido_em
            FROM fiinfra_snapshot_revisions
            WHERE data = ?
            ORDER BY revisao_num DESC
            """,
            conn,
            params=(str(ref_date),),
        )
    if df.empty:
        return df

    snapshots = df["snapshot_json"].apply(json.loads)
    df["observacao"] = snapshots.apply(lambda row: row.get("observacao"))
    df["zona"] = snapshots.apply(lambda row: row.get("zona"))
    df["metodologia_version"] = snapshots.apply(lambda row: row.get("metodologia_version"))
    df["coletado_em"] = snapshots.apply(lambda row: row.get("coletado_em"))
    df["fundos_count"] = df["fundos_json"].apply(lambda value: len(json.loads(value)))
    return df


def load_fiinfra_snapshots(days: int = 252 * 3, db_path=None) -> pd.DataFrame:
    """Carrega historico da Regua FI-Infra, ordenado por data ASC."""
    with _conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT * FROM (SELECT * FROM fiinfra_snapshots ORDER BY data DESC LIMIT ?) t ORDER BY data ASC",
            conn,
            params=(days,),
            parse_dates=["data"],
        )
    return df


def load_fiinfra_fundos(ref_date: Optional[date] = None, db_path=None) -> pd.DataFrame:
    """Carrega dados por fundo para uma data ou para a foto mais recente."""
    date_str = str(ref_date) if ref_date else None
    with _conn(db_path) as conn:
        if date_str:
            df = pd.read_sql(
                "SELECT * FROM fiinfra_fundos_snapshot WHERE data = ? ORDER BY ticker",
                conn,
                params=(date_str,),
            )
        else:
            latest = conn.execute(
                "SELECT MAX(data) FROM fiinfra_fundos_snapshot"
            ).fetchone()[0]
            if not latest:
                return pd.DataFrame()
            df = pd.read_sql(
                "SELECT * FROM fiinfra_fundos_snapshot WHERE data = ? ORDER BY ticker",
                conn,
                params=(latest,),
            )
    return df


def load_fiinfra_tranches(limit: int = 100, db_path=None) -> pd.DataFrame:
    """Carrega as tranches mais recentes."""
    with _conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT * FROM fiinfra_tranches ORDER BY data DESC, id DESC LIMIT ?",
            conn,
            params=(limit,),
            parse_dates=["data"],
        )
    return df


def _archive_fiinfra_revision(conn: sqlite3.Connection, data_str: str) -> None:
    snapshot = _fetch_one_dict(
        conn,
        "SELECT * FROM fiinfra_snapshots WHERE data = ?",
        (data_str,),
    )
    if not snapshot:
        return
    fundos = _fetch_all_dicts(
        conn,
        "SELECT * FROM fiinfra_fundos_snapshot WHERE data = ? ORDER BY ticker",
        (data_str,),
    )
    revisao_num = conn.execute(
        "SELECT COALESCE(MAX(revisao_num), 0) + 1 FROM fiinfra_snapshot_revisions WHERE data = ?",
        (data_str,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO fiinfra_snapshot_revisions
          (data, revisao_num, snapshot_json, fundos_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            data_str,
            revisao_num,
            json.dumps(snapshot, ensure_ascii=False, default=str),
            json.dumps(fundos, ensure_ascii=False, default=str),
        ),
    )


def _fetch_one_dict(
    conn: sqlite3.Connection,
    query: str,
    params: tuple = (),
) -> Optional[dict]:
    cursor = conn.execute(query, params)
    row = cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _fetch_all_dicts(
    conn: sqlite3.Connection,
    query: str,
    params: tuple = (),
) -> list[dict]:
    cursor = conn.execute(query, params)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    """Adiciona colunas novas sem quebrar bancos criados por versoes anteriores."""
    existentes = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for nome, tipo in columns.items():
        if nome not in existentes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {nome} {tipo}")
