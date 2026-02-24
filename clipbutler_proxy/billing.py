"""
Billing stubs — subscription quotas are enforced in usage.py.
This module is a placeholder for future Stripe webhook handling
(e.g., tier upgrades, cancellations) and metered billing if needed.
"""

import logging

logger = logging.getLogger(__name__)


def notify_quota_exceeded(license_key: str, tier: str):
    """Log a quota-exceeded event. Hook up Stripe/email here if needed."""
    logger.warning(f"Quota exceeded: license={license_key[:8]}… tier={tier}")


def record_usage_event(license_key: str, duration_sec: float, tier: str):
    """
    Optional: fire a Stripe metered billing event for overages.
    Currently a no-op since quotas are enforced at the subscription level.
    """
    pass
