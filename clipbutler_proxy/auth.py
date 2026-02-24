"""
License validation + session token management.
Validates against Keygen.sh with a 5-minute in-process cache.
"""

import os
import time
import uuid
import logging
import requests
from functools import lru_cache
from typing import Tuple

logger = logging.getLogger(__name__)

KEYGEN_ACCOUNT = os.environ.get("KEYGEN_ACCOUNT_ID", "")
KEYGEN_TOKEN = os.environ.get("KEYGEN_API_TOKEN", "")
KEYGEN_POLICY = os.environ.get("KEYGEN_POLICY_ID", "")

TIER_QUOTAS = {
    "starter": 7_200,    # 2 hours in seconds
    "pro": 36_000,       # 10 hours
    "studio": None,      # unlimited
}


# Simple dict-based cache: {license_key: (tier, validated_at)}
_license_cache: dict[str, Tuple[str, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def validate_license(license_key: str) -> Tuple[bool, str]:
    """
    Validate license key against Keygen.sh.
    Returns (is_valid, tier_name).
    Uses a 5-minute cache.
    """
    now = time.time()
    cached = _license_cache.get(license_key)
    if cached and now - cached[1] < _CACHE_TTL:
        return True, cached[0]

    if not KEYGEN_ACCOUNT or not KEYGEN_TOKEN:
        logger.warning("Keygen credentials not configured; accepting all keys in dev mode")
        _license_cache[license_key] = ("pro", now)
        return True, "pro"

    try:
        url = f"https://api.keygen.sh/v1/accounts/{KEYGEN_ACCOUNT}/licenses/actions/validate-key"
        resp = requests.post(
            url,
            json={"meta": {"key": license_key}},
            headers={
                "Authorization": f"Bearer {KEYGEN_TOKEN}",
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            },
            timeout=10,
        )
        data = resp.json()
        meta = data.get("meta", {})
        is_valid = meta.get("valid", False)

        if not is_valid:
            return False, ""

        # Extract tier from policy or license metadata
        attrs = data.get("data", {}).get("attributes", {})
        metadata = attrs.get("metadata", {})
        tier = metadata.get("tier", "starter").lower()
        if tier not in TIER_QUOTAS:
            tier = "starter"

        _license_cache[license_key] = (tier, now)
        return True, tier

    except Exception as e:
        logger.error(f"License validation error: {e}")
        # If already cached (even expired), allow with grace
        if cached:
            return True, cached[0]
        return False, ""


def create_session_token() -> str:
    return str(uuid.uuid4())
