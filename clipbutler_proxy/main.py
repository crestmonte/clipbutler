"""
ClipButler Proxy Service — FastAPI cloud app.

Flow:
  POST /session  → validate subscription → return upload URL (this server)
  PUT  /upload/{session_id} → receive proxy video/image, store in /tmp
  POST /analyze  → upload tmp file to Gemini Files API → get description
                   → delete tmp file + Gemini file → return description
  POST /stripe/webhook → handle subscription lifecycle events
  GET  /my-license → subscriber self-service license key lookup
"""

import os
import uuid
import tempfile
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import stripe
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import gemini as gemini_client
from auth import (
    validate_license, init_subscribers_db,
    upsert_subscriber, set_active, get_by_email,
)

# Stripe config
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Map Stripe product IDs → tier names
PRODUCT_TIERS = {
    "prod_U496dfnU1iuaqa": "enterprise",
    "prod_U493cGiP0FJ41g": "studio",
    "prod_U4910kxc5dnWp9": "freelancer",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clipbutler_proxy")

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

# Public URL of this service — used to build upload URLs returned to clients.
# Railway sets RAILWAY_PUBLIC_DOMAIN automatically; fall back for local dev.
_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
SERVICE_URL = f"https://{_domain}" if _domain else os.environ.get("SERVICE_URL", "http://localhost:8000")

# In-memory session store: {session_id: {"license_key", "tmp_path"}}
_sessions: dict[str, dict] = {}

UPLOAD_EXPIRY = 600  # seconds (informational — no hard enforcement needed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_subscribers_db()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        gemini_client.configure(api_key)
    elif not DEV_MODE:
        logger.warning("GEMINI_API_KEY not set — analysis will fail in production")

    logger.info(f"ClipButler Proxy started (dev_mode={DEV_MODE}, service_url={SERVICE_URL})")
    yield


app = FastAPI(title="ClipButler Proxy", version="2.0.0", lifespan=lifespan)


# ---------- Auth ----------

async def require_license(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    key = authorization.removeprefix("Bearer ").strip()
    if not validate_license(key):
        raise HTTPException(status_code=401, detail="Invalid or inactive subscription")
    return key


# ---------- Models ----------

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


# ---------- Endpoints ----------

@app.get("/health")
async def health():
    return {"status": "ok", "dev_mode": DEV_MODE}


@app.post("/session", response_model=SessionResponse)
async def create_session(req: SessionRequest):
    """Validate subscription, return a URL to PUT the proxy file to."""
    if not validate_license(req.license_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive subscription")

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {"license_key": req.license_key, "tmp_path": None}

    upload_url = f"{SERVICE_URL}/upload/{session_id}"
    logger.info(f"Session created: {session_id[:8]}… → {upload_url}")
    return SessionResponse(session_id=session_id, upload_url=upload_url, expires_in=UPLOAD_EXPIRY)


@app.put("/upload/{session_id}")
async def receive_upload(session_id: str, request: Request):
    """Receive the proxy video/image body and save it to a temp file."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    content_type = request.headers.get("content-type", "video/mp4")
    suffix = ".mp4"
    if "image" in content_type:
        ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif"}
        suffix = ext_map.get(content_type, ".jpg")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty upload body")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(body)
    tmp.close()
    session["tmp_path"] = tmp.name

    logger.info(f"Upload received: session={session_id[:8]}… size={len(body)} bytes → {tmp.name}")
    return {"ok": True}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, license_key: str = Depends(require_license)):
    """Upload tmp file to Gemini, get description, clean up everything."""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if session["license_key"] != license_key:
        raise HTTPException(status_code=403, detail="Session does not belong to this license")

    tmp_path = session.get("tmp_path")
    if not tmp_path or not os.path.exists(tmp_path):
        raise HTTPException(status_code=422, detail="Upload not found; PUT the file to /upload/{session_id} first")

    # Determine MIME type from file extension
    ext = Path(tmp_path).suffix.lower()
    mime_map = {".mp4": "video/mp4", ".mov": "video/mp4",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".tif": "image/tiff", ".tiff": "image/tiff"}
    mime_type = mime_map.get(ext, "video/mp4")

    logger.info(f"Analyzing: session={req.session_id[:8]}… file_type={req.file_type} mime={mime_type}")

    gemini_file = None
    try:
        if DEV_MODE:
            description = gemini_client.analyze(None, req.file_type)
        else:
            gemini_file = gemini_client.upload_file(tmp_path, mime_type)
            description = gemini_client.analyze(gemini_file, req.file_type)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis error: {e}")
    finally:
        # Always clean up — tmp file and Gemini file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if gemini_file:
            gemini_client.delete_file(gemini_file.name)
        _sessions.pop(req.session_id, None)

    logger.info(f"Analysis complete for {req.filename or req.session_id[:8]}")
    return AnalyzeResponse(description=description)


# ---------- Stripe webhook ----------

def _tier_from_subscription(subscription: dict) -> str:
    """Extract tier name from a Stripe subscription object via product ID."""
    try:
        product_id = subscription["items"]["data"][0]["price"]["product"]
        return PRODUCT_TIERS.get(product_id, "freelancer")
    except (KeyError, IndexError):
        return "freelancer"


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        logger.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        event = stripe.Event.construct_from(
            {"type": "unknown", "data": {"object": {}}}, stripe.api_key
        )
        try:
            import json
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid payload")
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    sub = event["data"]["object"]
    customer_id = sub.get("customer")

    logger.info(f"Stripe event: {event_type} customer={customer_id}")

    if event_type == "customer.subscription.created":
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.get("email", "")
        tier = _tier_from_subscription(sub)
        license_key = upsert_subscriber(
            email=email,
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub["id"],
            tier=tier,
            active=True,
        )
        # Store license key in Stripe customer metadata for easy retrieval
        stripe.Customer.modify(customer_id, metadata={"clipbutler_license_key": license_key})
        logger.info(f"Subscriber created: {email} tier={tier} key={license_key[:8]}…")

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        set_active(customer_id, active=False)
        logger.info(f"Subscriber deactivated: customer={customer_id}")

    elif event_type in ("customer.subscription.resumed", "customer.subscription.updated"):
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.get("email", "")
        tier = _tier_from_subscription(sub)
        is_active = sub.get("status") in ("active", "trialing")
        upsert_subscriber(
            email=email,
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub["id"],
            tier=tier,
            active=is_active,
        )
        logger.info(f"Subscriber updated: {email} tier={tier} active={is_active}")

    return JSONResponse(content={"received": True})


# ---------- License self-service lookup ----------

@app.get("/my-license")
async def my_license(email: str):
    """
    Let a subscriber look up their license key by email.
    The ClipButler app calls this during onboarding after the user enters their email.
    """
    record = get_by_email(email)
    if not record:
        raise HTTPException(status_code=404, detail="No active subscription found for this email")
    if not record["active"]:
        raise HTTPException(status_code=402, detail="Subscription is inactive")
    return {
        "license_key": record["license_key"],
        "tier": record["tier"],
    }
