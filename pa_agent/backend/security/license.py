"""
License validation via ClipButler proxy.
Grace period: 7 days offline before disabling ingest.
Degraded mode: search works, ingest disabled.
"""

import time
import platform
import logging
from typing import Tuple, Optional

import requests

from .hardware import get_fingerprint

_device_id = get_fingerprint()
_device_name = platform.node()

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 7


def _validate_via_proxy(key: str, proxy_url: str) -> dict:
    """
    Validate license key and register device with the proxy.
    Returns the full response dict (valid, tier, error, devices_used, etc.).
    """
    try:
        resp = requests.post(
            f"{proxy_url}/validate",
            json={
                "license_key": key,
                "device_id": _device_id,
                "device_name": _device_name,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"valid": False}
    except requests.RequestException:
        raise  # re-raise so caller can apply grace period logic


class LicenseStatus:
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"
    GRACE = "grace"     # offline but within grace period
    DEGRADED = "degraded"  # ingest disabled, search ok
    DEVICE_LIMIT = "device_limit"  # too many devices registered


class LicenseManager:
    def __init__(self, config_manager):
        """Accepts a ConfigManager instance (not a dict) so we can persist changes."""
        self._config_manager = config_manager
        self._last_valid_ts: Optional[float] = config_manager.get("license_last_valid_ts")
        self._status = LicenseStatus.INVALID
        self._ingest_allowed = False

    def validate(self, key: str) -> Tuple[str, str]:
        """
        Validate license key against the ClipButler proxy.
        Returns (status, message).
        """
        proxy_url = self._config_manager.get("proxy_url", "")

        if not proxy_url:
            # Dev mode: no proxy configured
            self._status = LicenseStatus.VALID
            self._ingest_allowed = True
            return LicenseStatus.VALID, "Development mode (no proxy configured)"

        try:
            result = _validate_via_proxy(key, proxy_url)

            if result.get("error") == "device_limit":
                self._status = LicenseStatus.DEVICE_LIMIT
                self._ingest_allowed = False
                return LicenseStatus.DEVICE_LIMIT, result.get(
                    "message", "Device limit reached (3 devices max)"
                )

            if result.get("valid"):
                self._last_valid_ts = time.time()
                # Persist to disk so grace period survives restart
                self._config_manager.update({"license_last_valid_ts": self._last_valid_ts})
                self._status = LicenseStatus.VALID
                self._ingest_allowed = True
                return LicenseStatus.VALID, "License valid"
            else:
                self._status = LicenseStatus.INVALID
                self._ingest_allowed = False
                return LicenseStatus.INVALID, "License key not found or subscription inactive"

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
