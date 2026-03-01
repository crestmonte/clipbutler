"""
Subscriber validation and management.
Checks a local SQLite DB for active subscriptions.
Populated by the Stripe webhook handler in main.py.
"""

import os
import time
import uuid
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SUBSCRIBERS_DB", "subscribers.db")

# In-process cache: {license_key: (is_active, checked_at)}
_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_subscribers_db():
    """Create subscribers table if it doesn't exist."""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                license_key          TEXT PRIMARY KEY,
                email                TEXT UNIQUE,
                active               INTEGER NOT NULL DEFAULT 1,
                tier                 TEXT NOT NULL DEFAULT 'freelancer',
                stripe_customer_id   TEXT UNIQUE,
                stripe_subscription_id TEXT UNIQUE,
                created_at           TEXT DEFAULT (datetime('now')),
                updated_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    logger.info("Subscribers DB ready")


def validate_license(license_key: str) -> bool:
    """
    Return True if the license_key belongs to an active subscriber.
    Falls back to accepting all keys when DB has no rows (dev/empty state).
    """
    if not license_key:
        return False

    now = time.time()
    cached = _cache.get(license_key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        with _get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
            if total == 0:
                logger.warning("No subscribers in DB — accepting all keys (dev mode)")
                _cache[license_key] = (True, now)
                return True

            row = conn.execute(
                "SELECT active FROM subscribers WHERE license_key = ?",
                (license_key,),
            ).fetchone()
            is_active = bool(row and row["active"])
            _cache[license_key] = (is_active, now)
            return is_active

    except Exception as e:
        logger.error(f"Subscriber check failed: {e}")
        return cached[0] if cached else False


def get_tier(license_key: str) -> str:
    """Return the tier for a license key ('freelancer', 'studio', 'enterprise')."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT tier FROM subscribers WHERE license_key = ?",
                (license_key,),
            ).fetchone()
            return row["tier"] if row else "freelancer"
    except Exception:
        return "freelancer"


def upsert_subscriber(
    email: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    tier: str,
    active: bool,
) -> str:
    """
    Insert or update a subscriber record.
    Generates a new license key on first insert; preserves it on update.
    Returns the license key.
    """
    _cache.clear()  # invalidate cache on any change
    try:
        with _get_db() as conn:
            existing = conn.execute(
                "SELECT license_key FROM subscribers WHERE stripe_customer_id = ?",
                (stripe_customer_id,),
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE subscribers
                    SET email = ?, active = ?, tier = ?,
                        stripe_subscription_id = ?,
                        updated_at = datetime('now')
                    WHERE stripe_customer_id = ?
                """, (email, int(active), tier, stripe_subscription_id, stripe_customer_id))
                conn.commit()
                return existing["license_key"]
            else:
                license_key = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO subscribers
                        (license_key, email, active, tier,
                         stripe_customer_id, stripe_subscription_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (license_key, email, int(active), tier,
                      stripe_customer_id, stripe_subscription_id))
                conn.commit()
                return license_key

    except Exception as e:
        logger.error(f"upsert_subscriber failed: {e}")
        raise


def set_active(stripe_customer_id: str, active: bool):
    """Activate or deactivate a subscriber by Stripe customer ID."""
    _cache.clear()
    try:
        with _get_db() as conn:
            conn.execute("""
                UPDATE subscribers
                SET active = ?, updated_at = datetime('now')
                WHERE stripe_customer_id = ?
            """, (int(active), stripe_customer_id))
            conn.commit()
    except Exception as e:
        logger.error(f"set_active failed: {e}")
        raise


def get_by_email(email: str) -> Optional[dict]:
    """Look up a subscriber record by email. Returns None if not found."""
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT license_key, tier, active FROM subscribers WHERE email = ?",
                (email,),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_by_email failed: {e}")
        return None
