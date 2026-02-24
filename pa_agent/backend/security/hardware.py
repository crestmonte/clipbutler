"""
Hardware fingerprinting for license binding.
Generates a stable machine ID from CPU/network/platform info.
"""

import platform
import hashlib
import uuid


def get_fingerprint() -> str:
    """
    Return a 32-char hex fingerprint stable across reboots.
    Uses: hostname + MAC address + processor string.
    """
    raw = f"{platform.node()}{uuid.getnode()}{platform.processor()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
