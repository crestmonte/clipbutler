"""
FastAPI server for ClipButler.
Serves the REST API on port 8765 and the control UI at /ui.
"""

import os
import logging
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .routes.search import make_search_router
from .routes.ingest import make_ingest_router
from .routes.settings import make_settings_router

logger = logging.getLogger(__name__)

# UI directory relative to this file
UI_DIR = Path(__file__).parent.parent.parent / "ui"


class FaceLabelRequest(BaseModel):
    cluster_id: str
    name: str


def create_app(sqlite_db, vector_db, scanner=None, config_manager=None, license_manager=None) -> FastAPI:
    app = FastAPI(
        title="ClipButler API",
        version="1.0.0",
        docs_url="/api/docs",
    )

    # CORS — allow localhost origins for NLE panels
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8765",
            "http://localhost:3000",
            "http://127.0.0.1:8765",
            "null",  # CEP panels load from file:// (null origin)
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Register route groups ----
    app.include_router(make_search_router(sqlite_db, vector_db))
    app.include_router(make_ingest_router(sqlite_db, scanner, config_manager))
    if config_manager:
        app.include_router(make_settings_router(config_manager, license_manager))

    # ---- Thumbnail endpoint ----
    @app.get("/api/thumbnail/{video_id}")
    async def get_thumbnail(video_id: str, t: float = 5.0):
        """
        Extract and return a JPEG thumbnail from the original video.
        Uses FFmpeg to grab frame at t seconds.
        """
        video = sqlite_db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        filepath = video["filepath"]
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Video file not found on disk")

        # Clamp t to within the video duration
        duration = video.get("duration_sec") or 0
        t = min(max(0, t), max(0, duration - 1)) if duration > 0 else 0

        thumb_path = f"/tmp/clipbutler_thumb_{video_id}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", str(t), "-i", filepath,
            "-vframes", "1", "-q:v", "3",
            "-vf", "scale=640:-1",
            thumb_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0 or not os.path.exists(thumb_path):
            raise HTTPException(status_code=500, detail="Thumbnail generation failed")

        return FileResponse(thumb_path, media_type="image/jpeg")

    # ---- Face labeling endpoint ----
    @app.post("/api/face/label")
    async def label_face(req: FaceLabelRequest):
        sqlite_db.label_face_cluster(req.cluster_id, req.name)
        # Update ChromaDB metadata too
        # (ChromaDB doesn't support metadata-only updates easily; we leave it for now
        #  and resolve via SQLite for display purposes)
        return {"status": "ok", "cluster_id": req.cluster_id, "name": req.name}

    @app.get("/api/faces")
    async def get_faces():
        clusters = sqlite_db.get_face_clusters()
        return {"clusters": clusters}

    # ---- License lookup (proxies to cloud service) ----
    @app.get("/api/license-lookup")
    def license_lookup(email: str):
        """Look up a subscriber's license key by email via the ClipButler proxy."""
        import requests as _req
        proxy_url = (
            config_manager.get("proxy_url", "https://clipbutler-production.up.railway.app")
            if config_manager else "https://clipbutler-production.up.railway.app"
        )
        try:
            r = _req.get(f"{proxy_url}/my-license", params={"email": email}, timeout=10)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 404:
                raise HTTPException(status_code=404, detail="No active subscription found for this email")
            elif r.status_code == 402:
                raise HTTPException(status_code=402, detail="Subscription is inactive — check your billing")
            else:
                raise HTTPException(status_code=502, detail="License lookup failed")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Cannot reach license server: {e}")

    # ---- Serve the control UI ----
    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    @app.get("/")
    async def root():
        return {"service": "ClipButler", "version": "1.0.0", "ui": "/ui", "docs": "/api/docs"}

    return app
