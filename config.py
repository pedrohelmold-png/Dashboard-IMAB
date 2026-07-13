"""
config.py — constantes e configurações do dashboard IMA-B 5.
Edite aqui para ajustar comportamento sem mexer no código.
"""
import os
from pathlib import Path

# ── Diretórios ─────────────────────────────────────────────────
ROOT     = Path(__file__).parent
DATA_DIR = ROOT / "data"
# Defina IMAB_DB_PATH para que o Streamlit e os ETLs usem o mesmo volume
# persistente em uma implantacao. Sem a variavel, preserva o banco local.
DB_PATH  = Path(os.environ.get("IMAB_DB_PATH", DATA_DIR / "imab5.db")).expanduser()

# ── Parâmetros do índice ───────────────────────────────────────
# IMA-B 5: títulos com vencimento até N anos à frente
IMAB5_CUTOFF_YEARS = 5

# Convenção de dias úteis (renda fixa BR)
WORKING_DAYS_YEAR = 252

# Ponderação do índice proxy (sem carteira oficial ANBIMA):
#   "duration" → peso proporcional à duration de cada título
#   "equal"    → peso igual para todos os títulos
WEIGHTING = "duration"

# ── Backfill no primeiro run ────────────────────────────────────
# Número de dias úteis a buscar retroativamente na primeira execução
BACKFILL_DAYS = 126  # ~6 meses; aumente para mais histórico (mais lento)

# ── Cores do dashboard ─────────────────────────────────────────
COLOR_CARREGO = "#2563EB"   # azul  — carrego nominal
COLOR_CDI     = "#DC2626"   # vermelho — CDI
COLOR_REAL    = "#16A34A"   # verde — taxa real
