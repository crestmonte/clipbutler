"""
License validation via Keygen.sh API.
Grace period: 7 days offline before disabling ingest.
Degraded mode: search works, ingest disabled.
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional

import requests

from .hardware import get_fingerprint

logger = logging.getLogger(__name__)

KEYGEN_ACCOUNT_ID = os.getenv("KEYGEN_ACCOUNT_ID", "")
VALIDATE_URL = f"https://api.keygen.sh/v1/accounts/{KEYGEN_ACCOUNT_ID}/licenses/actions/validate-key"
GRACE_PERIOD_DAYS = 7


class LicenseStatus:
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"
    GRACE = "grace"     # offline but within grace period
    DEGRADED = "degraded"  # ingest disabled, search ok


class LicenseManager:
    def __init__(self, config: dict):
        self.config = config
        self._last_valid_ts: Optional[float] = config.get("license_last_valid_ts")
        self._status = LicenseStatus.INVALID
        self._ingest_allowed = False

    def validate(self, key: str) -> Tuple[str, str]:
        """
        Validate license key against Keygen.sh.
        Returns (status, message).
        """
        fingerprint = get_fingerprint()

        if not KEYGEN_ACCOUNT_ID:
            # Dev mode: no license server configured
            self._status = LicenseStatus.VALID
            self._ingest_allowed = True
            return LicenseStatus.VALID, "Development mode (no license server)"

        try:
            resp = requests.post(
                VALIDATE_URL,
                json={"meta": {"key": key, "scope": {"fingerprint": fingerprint}}},
                headers={
                    "Content-Type": "application/vnd.api+json",
                    "Accept": "application/vnd.api+json",
                },
                timeout=10,
            )
            data = resp.json()
            meta = data.get("meta", {})
            valid = meta.get("valid", False)
            detail = meta.get("detail", "Unknown response")

            if valid:
                self._last_valid_ts = time.time()
                self.config["license_last_valid_ts"] = self._last_valid_ts
                self._status = LicenseStatus.VALID
                self._ingest_allowed = True
                return LicenseStatus.VALID, "License valid"
            else:
                self._status = LicenseStatus.INVALID
                self._ingest_allowed = False
                return LicenseStatus.INVALID, detail

        except requests.RequestException as e:
            logger.warning(f"License validation network error: {e}")
            return self._handle_offline()

    def _handle_offline(self) -> Tuple[str, str]:
        if self._last_valid_ts is None:
            self._status = LicenseStatus.INVALID
            self._ingest_allowed = False
            return LicenseStatus.INVALID, "Cannot reach license server and no prior validation"

        days_since = (time.time() - self._last_valid_ts) / 86400
        if days_since <= GRACE_PERIOD_DAYS:
            self._status = LicenseStatus.GRACE
            self._ingest_allowed = True
            remaining = int(GRACE_PERIOD_DAYS - days_since)
            return LicenseStatus.GRACE, f"Offline grace period: {remaining} days remaining"
        else:
            self._status = LicenseStatus.DEGRADED
            self._ingest_allowed = False
            return LicenseStatus.DEGRADED, "Grace period expired. Ingest disabled. Connect to internet to revalidate."

    @property
    def ingest_allowed(self) -> bool:
        return self._ingest_allowed

    @property
    def status(self) -> str:
        return self._status
