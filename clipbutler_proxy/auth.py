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
import threading
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Use /data/ for Railway persistent volume, fallback to local for dev
_DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else ".")
DB_PATH = os.environ.get("SUBSCRIBERS_DB", os.path.join(_DATA_DIR, "subscribers.db"))

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

# In-process cache: {license_key: (is_active, checked_at)}
_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX_SIZE = 10000  # prevent unbounded growth


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_subscribers_db():
    """Create subscribers table if it doesn't exist."""
    logger.info(f"Subscribers DB path: {DB_PATH}")
    conn = _get_db()
    try:
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key TEXT NOT NULL REFERENCES subscribers(license_key),
                device_id   TEXT NOT NULL,
                device_name TEXT DEFAULT '',
                first_seen  TEXT DEFAULT (datetime('now')),
                last_seen   TEXT DEFAULT (datetime('now')),
                UNIQUE(license_key, device_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_devices_license ON devices(license_key)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key  TEXT NOT NULL REFERENCES subscribers(license_key),
                duration_sec REAL NOT NULL,
                filename     TEXT DEFAULT '',
                logged_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_license ON usage_log(license_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_logged ON usage_log(logged_at)"
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Subscribers DB ready")


def validate_license(license_key: str) -> bool:
    """
    Return True if the license_key belongs to an active subscriber.
    In DEV_MODE with an empty DB, accepts all keys for local testing.
    """
    if not license_key:
        return False

    now = time.time()
    cached = _cache.get(license_key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        conn = _get_db()
        try:
            # Only accept all keys in explicit DEV_MODE with empty DB
            if DEV_MODE:
                total = conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
                if total == 0:
                    logger.warning("DEV MODE: No subscribers in DB — accepting all keys")
                    _cache[license_key] = (True, now)
                    return True

            row = conn.execute(
                "SELECT active FROM subscribers WHERE license_key = ?",
                (license_key,),
            ).fetchone()
            is_active = bool(row and row["active"])
            # Evict oldest entries if cache is too large
            if len(_cache) >= _CACHE_MAX_SIZE:
                oldest_key = min(_cache, key=lambda k: _cache[k][1])
                _cache.pop(oldest_key, None)
            _cache[license_key] = (is_active, now)
            return is_active
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"Subscriber check failed: {e}")
        return cached[0] if cached else False


def get_tier(license_key: str) -> str:
    """Return the tier for a license key ('freelancer', 'studio', 'enterprise')."""
    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT tier FROM subscribers WHERE license_key = ?",
                (license_key,),
            ).fetchone()
            return row["tier"] if row else "freelancer"
        finally:
            conn.close()
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
    conn = _get_db()
    try:
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
    finally:
        conn.close()


def set_active(stripe_customer_id: str, active: bool):
    """Activate or deactivate a subscriber by Stripe customer ID."""
    _cache.clear()
    conn = _get_db()
    try:
        conn.execute("""
            UPDATE subscribers
            SET active = ?, updated_at = datetime('now')
            WHERE stripe_customer_id = ?
        """, (int(active), stripe_customer_id))
        conn.commit()
    except Exception as e:
        logger.error(f"set_active failed: {e}")
        raise
    finally:
        conn.close()


def get_by_email(email: str) -> Optional[dict]:
    """Look up a subscriber record by email. Returns None if not found."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT license_key, tier, active FROM subscribers WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_by_email failed: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Device binding
# ---------------------------------------------------------------------------

MAX_DEVICES = 3
_DEVICE_EXPIRY_DAYS = 90


def register_device(
    license_key: str, device_id: str, device_name: str = ""
) -> tuple[bool, str, int]:
    """
    Register a device for a license key.
    Returns (ok, message, devices_used).
    Automatically expires devices not seen for 90 days.
    """
    conn = _get_db()
    try:
        # Auto-expire stale devices
        conn.execute(
            "DELETE FROM devices WHERE license_key = ? "
            "AND last_seen < datetime('now', ?)",
            (license_key, f"-{_DEVICE_EXPIRY_DAYS} days"),
        )

        # Already registered? Update last_seen.
        existing = conn.execute(
            "SELECT id FROM devices WHERE license_key = ? AND device_id = ?",
            (license_key, device_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE devices SET last_seen = datetime('now'), "
                "device_name = ? WHERE license_key = ? AND device_id = ?",
                (device_name, license_key, device_id),
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM devices WHERE license_key = ?",
                (license_key,),
            ).fetchone()[0]
            return True, "ok", count

        # Check device count
        count = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE license_key = ?",
            (license_key,),
        ).fetchone()[0]
        if count >= MAX_DEVICES:
            return (
                False,
                f"Device limit reached ({count}/{MAX_DEVICES}). "
                "Deregister a device or wait for auto-expiry.",
                count,
            )

        # Register new device
        conn.execute(
            "INSERT INTO devices (license_key, device_id, device_name) "
            "VALUES (?, ?, ?)",
            (license_key, device_id, device_name),
        )
        conn.commit()
        return True, "ok", count + 1

    except Exception as e:
        logger.error(f"register_device failed: {e}")
        return False, str(e), 0
    finally:
        conn.close()


def get_devices(license_key: str) -> list[dict]:
    """Return all registered devices for a license key."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT device_id, device_name, first_seen, last_seen "
            "FROM devices WHERE license_key = ? ORDER BY last_seen DESC",
            (license_key,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_devices failed: {e}")
        return []
    finally:
        conn.close()


def device_is_registered(license_key: str, device_id: str) -> bool:
    """Check if a specific device is registered for a license."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM devices WHERE license_key = ? AND device_id = ?",
            (license_key, device_id),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def remove_device(license_key: str, device_id: str) -> bool:
    """Remove a device registration. Returns True if a row was deleted."""
    conn = _get_db()
    try:
        cur = conn.execute(
            "DELETE FROM devices WHERE license_key = ? AND device_id = ?",
            (license_key, device_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"remove_device failed: {e}")
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------

_TIER_LIMITS_SEC = {
    "freelancer": 30 * 3600,    # 30 hours
    "studio": 80 * 3600,        # 80 hours
    "enterprise": 250 * 3600,   # 250 hours
}


def get_tier_limit_sec(tier: str) -> float:
    """Return the monthly usage limit in seconds for a tier."""
    return _TIER_LIMITS_SEC.get(tier, float("inf"))


def log_usage(license_key: str, duration_sec: float, filename: str = ""):
    """Record a completed analysis in the usage log."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO usage_log (license_key, duration_sec, filename) "
            "VALUES (?, ?, ?)",
            (license_key, duration_sec, filename),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"log_usage failed: {e}")
    finally:
        conn.close()


def _billing_period_start(license_key: str) -> str:
    """
    Return ISO date string for the start of the current billing period.
    Uses the subscriber's created_at day as the anchor.
    Falls back to 1st of the current month.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT created_at FROM subscribers WHERE license_key = ?",
            (license_key,),
        ).fetchone()
        if not row or not row["created_at"]:
            return date.today().replace(day=1).isoformat()

        created = datetime.fromisoformat(row["created_at"])
        anchor_day = created.day
        today = date.today()

        if today.day >= anchor_day:
            try:
                period_start = today.replace(day=anchor_day)
            except ValueError:
                period_start = today.replace(day=1)
        else:
            first = today.replace(day=1)
            prev_month_end = first - timedelta(days=1)
            try:
                period_start = prev_month_end.replace(
                    day=min(anchor_day, prev_month_end.day)
                )
            except ValueError:
                period_start = prev_month_end.replace(day=1)

        return period_start.isoformat()
    except Exception as e:
        logger.error(f"_billing_period_start failed: {e}")
        return date.today().replace(day=1).isoformat()
    finally:
        conn.close()


def get_monthly_usage_sec(license_key: str) -> float:
    """Return total seconds of footage analyzed in the current billing period."""
    period_start = _billing_period_start(license_key)
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(duration_sec), 0) as total "
            "FROM usage_log WHERE license_key = ? AND logged_at >= ?",
            (license_key, period_start),
        ).fetchone()
        return float(row["total"])
    except Exception as e:
        logger.error(f"get_monthly_usage_sec failed: {e}")
        return 0.0
    finally:
        conn.close()


def get_usage_summary(license_key: str) -> dict:
    """Return a usage summary dict for the current billing period."""
    tier = get_tier(license_key)
    limit_sec = get_tier_limit_sec(tier)
    used_sec = get_monthly_usage_sec(license_key)
    period_start = _billing_period_start(license_key)

    # Calculate period end (roughly +1 month)
    ps = date.fromisoformat(period_start)
    if ps.month == 12:
        period_end = date(ps.year + 1, 1, min(ps.day, 28))
    else:
        import calendar
        max_day = calendar.monthrange(ps.year, ps.month + 1)[1]
        period_end = date(ps.year, ps.month + 1, min(ps.day, max_day))

    return {
        "used_hours": round(used_sec / 3600, 1),
        "limit_hours": round(limit_sec / 3600, 1) if limit_sec != float("inf") else None,
        "remaining_hours": round(max(0, limit_sec - used_sec) / 3600, 1)
            if limit_sec != float("inf") else None,
        "period_start": period_start,
        "period_end": period_end.isoformat(),
    }
