"""Configuração global: caminhos, fuso horário e preferências (config/settings.toml)."""
from __future__ import annotations

import os
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MILHAS_DATA_DIR", ROOT / "data"))
SITE_DIR = Path(os.environ.get("MILHAS_SITE_DIR", ROOT / "site"))
DB_PATH = Path(os.environ.get("MILHAS_DB", DATA_DIR / "milhas.db"))
SETTINGS_PATH = Path(os.environ.get("MILHAS_SETTINGS", ROOT / "config" / "settings.toml"))
WEB_TEMPLATE = ROOT / "web" / "index.html"

TZ = ZoneInfo("America/Sao_Paulo")

USER_AGENT = os.environ.get(
    "MILHAS_USER_AGENT",
    "MilhasMonitor/1.0 (monitor pessoal de promocoes; respeita robots.txt; 1 req/3s por site)",
)


def now() -> datetime:
    """Agora, com fuso America/Sao_Paulo. MILHAS_NOW (ISO) permite simular o relógio em testes."""
    fake = os.environ.get("MILHAS_NOW")
    if fake:
        dt = datetime.fromisoformat(fake)
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
    return datetime.now(TZ)


_DEFAULTS = {
    "monitor": {
        "max_items_per_source": 30,
        "max_article_fetches_per_source": 8,
        "per_host_interval_seconds": 3.0,
        "timeout_seconds": 20,
        "cooldown_after_errors": 5,
        "cooldown_minutes": 180,
        "undated_promo_max_age_days": 7,
        "official_verify_per_run": 6,
    },
    "alerts": {
        "new_promo": True,
        "new_promo_categories": ["transferencia", "compra_bonificada", "compra_pontos", "clube", "cartao", "resgate", "outra"],
        "transfer_min_bonus": 80,
        "ppr_min": 10,
        "milheiro_max": 30.0,
        "club_special": True,
        "ending_hours": 24,
        "changes": True,
        "ppr_above_history_factor": 1.5,
        "programs": [],
    },
    "reports": {"daily_hour": 8, "weekly_weekday": 0, "weekly_hour": 8},
    "site": {"title": "Central de Pontos e Milhas", "ended_days_visible": 30},
}


def load_settings() -> dict:
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as fh:
            user = tomllib.load(fh)
        for section, values in user.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
            else:
                cfg[section] = values
    return cfg
