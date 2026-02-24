"""
Settings API routes.
GET/POST /api/settings — manage watch paths, config.
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    watch_paths: Optional[List[str]] = None
    whisper_model: Optional[str] = None
    license_key: Optional[str] = None
    project_name: Optional[str] = None
    proxy_url: Optional[str] = None  # override for developers/self-hosters


def make_settings_router(config_manager, license_manager=None):
    """Factory: create the settings router with injected config manager."""

    @router.get("")
    async def get_settings():
        cfg = config_manager.get_all()
        return cfg

    @router.post("")
    async def update_settings(update: SettingsUpdate):
        changed = {}
        if update.watch_paths is not None:
            changed["watch_paths"] = update.watch_paths

        if update.whisper_model is not None:
            allowed = ["tiny", "base", "small", "medium", "large"]
            if update.whisper_model not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"whisper_model must be one of {allowed}"
                )
            changed["whisper_model"] = update.whisper_model

        if update.license_key and license_manager:
            status, msg = license_manager.validate(update.license_key)
            changed["license_key"] = update.license_key
            changed["license_status"] = status

        if update.project_name is not None:
            changed["project_name"] = update.project_name

        if update.proxy_url is not None:
            changed["proxy_url"] = update.proxy_url

        if changed:
            config_manager.update(changed)

        return {"status": "ok", "updated_keys": list(changed.keys())}

    return router
