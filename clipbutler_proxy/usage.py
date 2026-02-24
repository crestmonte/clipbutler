"""
Per-license usage tracking against monthly tier quotas.
Uses SQLite (simple, no external DB required for single-instance deploy).
Switch to Postgres by changing the engine URL via DATABASE_URL env var.
"""

import os
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./usage.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

TIER_QUOTAS = {
    "starter": 7_200,
    "pro": 36_000,
    "studio": None,  # unlimited
}


def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS usage (
                license_key TEXT NOT NULL,
                year_month  TEXT NOT NULL,
                seconds_used REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (license_key, year_month)
            )
        """))
        conn.commit()


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def get_usage(license_key: str) -> float:
    """Return seconds used in the current billing month."""
    ym = _current_month()
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT seconds_used FROM usage WHERE license_key=:k AND year_month=:ym"),
            {"k": license_key, "ym": ym},
        ).fetchone()
    return float(row[0]) if row else 0.0


def add_usage(license_key: str, duration_sec: float):
    """Add seconds_used for the current billing month."""
    ym = _current_month()
    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO usage (license_key, year_month, seconds_used)
            VALUES (:k, :ym, :d)
            ON CONFLICT(license_key, year_month)
            DO UPDATE SET seconds_used = seconds_used + :d
        """), {"k": license_key, "ym": ym, "d": duration_sec})
        db.commit()


def check_quota(license_key: str, tier: str) -> tuple[bool, float, Optional[float]]:
    """
    Check whether the license has remaining quota.
    Returns (quota_ok, seconds_used, quota_limit_or_None).
    """
    limit = TIER_QUOTAS.get(tier)
    used = get_usage(license_key)
    if limit is None:
        return True, used, None
    return used < limit, used, float(limit)
