"""
Ingest status and queue API routes.
"""

import logging
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingest"])


class RetryRequest(BaseModel):
    video_id: str


def make_ingest_router(sqlite_db, scanner=None, config_manager=None):
    """Factory: create the ingest router with injected DB + scanner."""

    @router.get("/status")
    async def get_status():
        stats = sqlite_db.get_stats()

        # Fetch quota from proxy service (best-effort; non-blocking)
        quota_remaining_sec = None
        tier_name = None
        if config_manager:
            proxy_url = config_manager.get("proxy_url", "")
            license_key = config_manager.get("license_key", "")
            if proxy_url and license_key:
                try:
                    r = requests.get(
                        f"{proxy_url}/usage",
                        headers={"Authorization": f"Bearer {license_key}"},
                        timeout=5,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        tier_name = data.get("tier_name")
                        limit = data.get("tier_limit_sec")
                        used = data.get("seconds_used", 0)
                        quota_remaining_sec = (limit - used) if limit is not None else None
                except Exception as e:
                    logger.debug(f"Quota fetch failed: {e}")

        return {
            "queue_depth": stats["pending"],
            "indexed": stats["indexed"],
            "failed": stats["failed"],
            "processed_today": stats["processed_today"],
            "total": stats["total"],
            "scanner_running": scanner._running if scanner else False,
            "quota_remaining_sec": quota_remaining_sec,
            "tier_name": tier_name,
        }

    @router.get("/queue")
    async def get_queue():
        pending = sqlite_db.get_pending(max_retries=10)
        return {
            "items": [
                {
                    "id": v["id"],
                    "filename": v["filename"],
                    "status": v["status"],
                    "retry_count": v["retry_count"],
                    "error_log": v["error_log"],
                }
                for v in pending
            ]
        }

    @router.post("/retry")
    async def retry_video(req: RetryRequest):
        video = sqlite_db.get_video(req.video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        sqlite_db.set_status(req.video_id, "PENDING")
        return {"status": "queued"}

    return router
