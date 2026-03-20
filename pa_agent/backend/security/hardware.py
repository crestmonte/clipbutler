"""
Hardware fingerprinting for license binding.
Generates a stable machine ID from hostname + CPU info.
MAC address intentionally excluded — it changes with docking stations / VPNs.
"""

import platform
import hashlib


def get_fingerprint() -> str:
    """
    Return a 32-char hex fingerprint stable across reboots.
    Uses: hostname + CPU architecture + processor string.
    """
    raw = f"{platform.node()}{platform.machine()}{platform.processor()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
