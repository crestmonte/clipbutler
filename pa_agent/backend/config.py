"""
Configuration manager for ClipButler.
Reads/writes config.json. No API keys stored client-side — AI analysis
goes through the ClipButler proxy service which holds the operator key.
"""

import os
import json
import platform
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Platform-specific data directory
def get_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", Path.home())
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    d = Path(base) / "ClipButler"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS: Dict[str, Any] = {
    "watch_paths": [],
    "whisper_model": "base",
    "license_key": "",
    "license_status": "unknown",
    "license_last_valid_ts": None,
    "project_name": "",
    "proxy_url": "https://clipbutler-production.up.railway.app",
}


class ConfigManager:
    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = get_data_dir() / "config.json"

        self._data: Dict[str, Any] = dict(DEFAULTS)
        self._load()

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load config: {e}")

    def _save(self):
        try:
            # Atomic write: write to temp file, then rename
            tmp_path = self.config_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(self._data, f, indent=2)
            tmp_path.replace(self.config_path)
        except OSError as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        return dict(self._data)

    def update(self, values: Dict[str, Any]):
        self._data.update(values)
        self._save()

    @property
    def db_path(self) -> str:
        return str(get_data_dir() / "metadata.db")

    @property
    def chroma_path(self) -> str:
        return str(get_data_dir() / "chroma_data")

    @property
    def proxy_folder(self) -> str:
        folder = str(get_data_dir() / "proxies")
        Path(folder).mkdir(parents=True, exist_ok=True)
        return folder

    @property
    def thumbnail_folder(self) -> str:
        folder = str(get_data_dir() / "thumbnails")
        Path(folder).mkdir(parents=True, exist_ok=True)
        return folder
