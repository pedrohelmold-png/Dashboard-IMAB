"""Gera backup consistente do SQLite usado pelo aplicativo."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, DB_PATH


def backup_database(source: Path = DB_PATH, destination: Path | None = None) -> Path:
    """Copia o banco por meio da API SQLite, segura mesmo com WAL ativo."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Banco nao encontrado: {source}")
    if destination is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = DATA_DIR / "backups" / f"imab5-{stamp}.db"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria backup consistente do banco IMAB.")
    parser.add_argument("--destination", help="Caminho do arquivo de destino.")
    args = parser.parse_args()
    result = backup_database(destination=Path(args.destination) if args.destination else None)
    print(result)


if __name__ == "__main__":
    main()
