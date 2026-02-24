"""
ClipButler Proxy Service — FastAPI cloud app.

Handles:
- License validation (Keygen.sh)
- GCS presigned upload URL generation
- Gemini video/image analysis (operator key lives here)
- Per-license quota tracking
"""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env before importing modules that read os.environ at module level
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from auth import validate_license
from gcs import generate_upload_url, object_exists, delete_object, gcs_uri, DEV_MODE, DEV_DIR
from gemini import analyze_gcs_video, analyze_gcs_image
from usage import init_db, get_usage, add_usage, check_quota, TIER_QUOTAS
from billing import notify_quota_exceeded, record_usage_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clipbutler_proxy")

# In-memory session store: {session_id: {"license_key", "object_name", "tier"}}
_sessions: dict[str, dict] = {}

UPLOAD_EXPIRY = 600  # 10 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("ClipButler Proxy Service started")
    yield


app = FastAPI(
    title="ClipButler Proxy",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------- Auth helper ----------

async def require_license(authorization: str = Header(...)) -> tuple[str, str]:
    """Dependency: parse Bearer token, validate license, return (license_key, tier)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    license_key = authorization.removeprefix("Bearer ").strip()
    valid, tier = validate_license(license_key)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid or expired license key")
    return license_key, tier


# ---------- Request/response models ----------

class SessionRequest(BaseModel):
    license_key: str


class SessionResponse(BaseModel):
    session_id: str
    upload_url: str
    expires_in: int


class AnalyzeRequest(BaseModel):
    session_id: str
    duration_sec: float = 0.0
    filename: str = ""
    file_type: str = "video"  # "video" or "image"


class AnalyzeResponse(BaseModel):
    description: str


class UsageResponse(BaseModel):
    calls_month: int   # not tracked granularly, placeholder
    seconds_used: float
    tier_limit_sec: Optional[float]
    tier_name: str


# ---------- Endpoints ----------

@app.get("/health")
async def health():
    return {"status": "ok", "dev_mode": DEV_MODE}


# ---------- Dev-mode upload endpoint ----------
# In production, clients PUT directly to a GCS presigned URL.
# In dev mode, they PUT here instead so we can test without GCS.

@app.put("/dev-upload/{token}")
async def dev_upload(token: str, request: Request):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="Not available outside dev mode")
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEV_DIR / token
    body = await request.body()
    dest.write_bytes(body)
    logger.info(f"[DEV] Saved upload: {dest} ({len(body)} bytes)")
    return JSONResponse(status_code=200, content={"ok": True})


@app.post("/session", response_model=SessionResponse)
async def create_session(req: SessionRequest):
    """
    Validate license, create GCS presigned upload URL, return session_id.
    """
    valid, tier = validate_license(req.license_key)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid or expired license key")

    # Quota pre-check
    quota_ok, used, limit = check_quota(req.license_key, tier)
    if not quota_ok:
        notify_quota_exceeded(req.license_key, tier)
        raise HTTPException(
            status_code=402,
            detail=f"Monthly quota exceeded ({used:.0f}/{limit:.0f} sec). Upgrade your plan.",
        )

    object_name, signed_url = generate_upload_url(expiry_seconds=UPLOAD_EXPIRY)

    # Store session
    import uuid
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "license_key": req.license_key,
        "object_name": object_name,
        "tier": tier,
    }

    logger.info(f"Session created: {session_id[:8]}… license={req.license_key[:8]}… tier={tier}")
    return SessionResponse(
        session_id=session_id,
        upload_url=signed_url,
        expires_in=UPLOAD_EXPIRY,
    )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    req: AnalyzeRequest,
    auth: tuple[str, str] = Depends(require_license),
):
    """
    Verify upload is complete, run Gemini analysis, record usage, return description.
    """
    license_key, tier = auth

    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if session["license_key"] != license_key:
        raise HTTPException(status_code=403, detail="Session does not belong to this license")

    object_name = session["object_name"]

    # Verify the upload actually landed in GCS
    if not object_exists(object_name):
        raise HTTPException(status_code=422, detail="Upload not found in GCS; upload the file first")

    # Quota check again (in case concurrent requests raced)
    quota_ok, used, limit = check_quota(license_key, tier)
    if not quota_ok:
        delete_object(object_name)
        _sessions.pop(req.session_id, None)
        notify_quota_exceeded(license_key, tier)
        raise HTTPException(
            status_code=402,
            detail=f"Monthly quota exceeded ({used:.0f}/{limit:.0f} sec).",
        )

    uri = gcs_uri(object_name)
    logger.info(f"Analyzing {uri} (file_type={req.file_type}, duration={req.duration_sec:.1f}s)")

    try:
        if req.file_type == "image":
            description = analyze_gcs_image(uri)
        else:
            description = analyze_gcs_video(uri)
    except Exception as e:
        logger.error(f"Gemini analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis error: {e}")
    finally:
        # Always delete from GCS after analysis
        delete_object(object_name)
        _sessions.pop(req.session_id, None)

    # Record usage
    duration = req.duration_sec if req.duration_sec > 0 else 1.0
    add_usage(license_key, duration)
    record_usage_event(license_key, duration, tier)

    logger.info(f"Analysis complete for {req.filename or object_name}")
    return AnalyzeResponse(description=description)


@app.get("/usage", response_model=UsageResponse)
async def get_usage_endpoint(
    auth: tuple[str, str] = Depends(require_license),
):
    """Return current billing period usage for the authenticated license."""
    license_key, tier = auth
    used = get_usage(license_key)
    limit = TIER_QUOTAS.get(tier)
    return UsageResponse(
        calls_month=0,  # not tracked per-call in this implementation
        seconds_used=used,
        tier_limit_sec=float(limit) if limit is not None else None,
        tier_name=tier,
    )
