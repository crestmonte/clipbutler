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
import time
import asyncio
import tempfile
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import resend
import stripe
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
resend.api_key = RESEND_API_KEY

import gemini as gemini_client
from auth import (
    validate_license, get_tier, init_subscribers_db,
    upsert_subscriber, set_active, get_by_email,
    register_device, get_devices, device_is_registered, remove_device,
    log_usage, get_monthly_usage_sec, get_tier_limit_sec, get_usage_summary,
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

# In-memory session store: {session_id: {"license_key", "tmp_path", "created_at"}}
_sessions: dict[str, dict] = {}

UPLOAD_EXPIRY = 600  # seconds
MAX_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MB max upload size

# Simple in-memory rate limiter: {ip_or_key: [timestamps]}
_rate_limits: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30  # requests per window


def _check_rate_limit(key: str, max_requests: int = RATE_LIMIT_MAX):
    """Raise 429 if rate limit exceeded. Cleans up old entries."""
    now = time.time()
    timestamps = _rate_limits.get(key, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
    timestamps.append(now)
    _rate_limits[key] = timestamps
    # Periodic cleanup of stale keys
    if len(_rate_limits) > 10000:
        stale = [k for k, v in _rate_limits.items() if not v or now - v[-1] > RATE_LIMIT_WINDOW * 2]
        for k in stale:
            _rate_limits.pop(k, None)


async def _cleanup_expired_sessions():
    """Background task: purge sessions older than UPLOAD_EXPIRY, delete their temp files."""
    while True:
        await asyncio.sleep(60)  # check every minute
        now = time.time()
        expired = [
            sid for sid, s in _sessions.items()
            if now - s.get("created_at", 0) > UPLOAD_EXPIRY
        ]
        for sid in expired:
            session = _sessions.pop(sid, None)
            if session:
                tmp_path = session.get("tmp_path")
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                logger.info(f"Expired session cleaned up: {sid[:8]}…")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_subscribers_db()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        gemini_client.configure(api_key)
    elif not DEV_MODE:
        logger.error("MISSING CRITICAL VAR: GEMINI_API_KEY — analysis will fail")

    missing = []
    if not api_key and not DEV_MODE:
        missing.append("GEMINI_API_KEY")
    if not STRIPE_SECRET_KEY:
        missing.append("STRIPE_SECRET_KEY")
    if not STRIPE_WEBHOOK_SECRET:
        missing.append("STRIPE_WEBHOOK_SECRET")
    if not RESEND_API_KEY:
        missing.append("RESEND_API_KEY")
    if missing:
        logger.error(f"MISSING CRITICAL ENV VARS: {', '.join(missing)}")
    else:
        logger.info("All critical env vars present")

    logger.info(f"ClipButler Proxy started (dev_mode={DEV_MODE}, service_url={SERVICE_URL})")

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_expired_sessions())
    yield
    cleanup_task.cancel()


app = FastAPI(title="ClipButler Proxy", version="2.1.0", lifespan=lifespan)


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
    device_id: str = ""

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
    return {"status": "ok", "dev_mode": DEV_MODE, "active_sessions": len(_sessions)}


@app.post("/validate")
async def validate_license_endpoint(request: Request):
    """Check whether a license key is active. No auth required — this IS the auth check."""
    body = await request.json()
    key = body.get("license_key", "")
    if not key:
        raise HTTPException(status_code=422, detail="license_key required")

    _check_rate_limit(f"validate:{key}", max_requests=20)

    valid = validate_license(key)
    tier = get_tier(key) if valid else None

    result = {"valid": valid, "tier": tier}

    # Device binding (optional — old clients won't send device_id)
    device_id = body.get("device_id", "")
    if valid and device_id:
        device_name = body.get("device_name", "")
        ok, msg, devices_used = register_device(key, device_id, device_name)
        if not ok:
            return {
                "valid": False,
                "error": "device_limit",
                "message": msg,
                "devices": get_devices(key),
            }
        result["devices_used"] = devices_used
        result["devices_max"] = 3

    return result


@app.post("/session", response_model=SessionResponse)
async def create_session(req: SessionRequest):
    """Validate subscription, return a URL to PUT the proxy file to."""
    if not validate_license(req.license_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive subscription")

    _check_rate_limit(f"session:{req.license_key}", max_requests=10)

    # If client sent a device_id, verify it's registered
    if req.device_id and not device_is_registered(req.license_key, req.device_id):
        raise HTTPException(
            status_code=403,
            detail="Device not registered. Run /validate first.",
        )

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "license_key": req.license_key,
        "tmp_path": None,
        "created_at": time.time(),
    }

    upload_url = f"{SERVICE_URL}/upload/{session_id}"
    logger.info(f"Session created: {session_id[:8]}… → {upload_url}")
    return SessionResponse(session_id=session_id, upload_url=upload_url, expires_in=UPLOAD_EXPIRY)


@app.put("/upload/{session_id}")
async def receive_upload(session_id: str, request: Request):
    """Receive the proxy video/image body and save it to a temp file."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # Check Content-Length header if present
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large. Max {MAX_UPLOAD_BYTES // (1024*1024)} MB."
        )

    content_type = request.headers.get("content-type", "video/mp4")
    suffix = ".mp4"
    if "image" in content_type:
        ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif"}
        suffix = ext_map.get(content_type, ".jpg")

    # Stream the upload to disk with size enforcement
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    total_bytes = 0
    try:
        async for chunk in request.stream():
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload too large. Max {MAX_UPLOAD_BYTES // (1024*1024)} MB."
                )
            tmp.write(chunk)
        tmp.close()
    except HTTPException:
        raise
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Upload failed")

    if total_bytes == 0:
        os.unlink(tmp.name)
        raise HTTPException(status_code=400, detail="Empty upload body")

    session["tmp_path"] = tmp.name

    logger.info(f"Upload received: session={session_id[:8]}… size={total_bytes} bytes → {tmp.name}")
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

    # ---- Usage metering: pre-check before expensive Gemini call ----
    tier = get_tier(license_key)
    limit_sec = get_tier_limit_sec(tier)
    used_sec = get_monthly_usage_sec(license_key)
    if limit_sec != float("inf") and used_sec + req.duration_sec > limit_sec:
        summary = get_usage_summary(license_key)
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Monthly usage limit reached",
                **summary,
            },
        )

    gemini_file = None
    try:
        if DEV_MODE:
            description = gemini_client.analyze(None, req.file_type)
        else:
            # Run blocking Gemini calls in a thread to avoid blocking the event loop
            gemini_file = await asyncio.to_thread(
                gemini_client.upload_file, tmp_path, mime_type
            )
            description = await asyncio.to_thread(
                gemini_client.analyze, gemini_file, req.file_type
            )
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail="Analysis failed. Please retry.")
    finally:
        # Always clean up — tmp file and Gemini file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        if gemini_file:
            try:
                await asyncio.to_thread(gemini_client.delete_file, gemini_file.name)
            except Exception:
                pass
        _sessions.pop(req.session_id, None)

    # ---- Usage metering: log successful analysis ----
    log_usage(license_key, req.duration_sec, req.filename)

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


def _send_license_email(email: str, license_key: str, tier: str):
    """Send license key email with retry."""
    if not RESEND_API_KEY or not email:
        logger.warning(f"Cannot send license email: RESEND_API_KEY={'set' if RESEND_API_KEY else 'missing'}, email={email or 'empty'}")
        return

    for attempt in range(3):
        try:
            resend.Emails.send({
                "from": "ClipButler <hello@clipbutler.com>",
                "to": email,
                "subject": "Your ClipButler license key",
                "html": (
                    f"<p>Thanks for subscribing to ClipButler ({tier})!</p>"
                    f"<p>Your license key is:</p><pre>{license_key}</pre>"
                    f"<p><a href='https://github.com/crestmonte/clipbutler/releases/latest'>Download CLPBTLR for Mac</a> — open the DMG and drag CLPBTLR to Applications, then run setup and enter your key when prompted.</p>"
                    f"<p>Need your key again? <a href='{SERVICE_URL}/my-license?email={email}'>Retrieve it here</a>.</p>"
                ),
            })
            logger.info(f"License key email sent to {email}")
            return
        except Exception as e:
            logger.error(f"Failed to send license email to {email} (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    logger.error(f"CRITICAL: License email permanently failed for {email}, key={license_key[:8]}…")


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.warning(f"Webhook signature verification failed: {e}")
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

        # Email the key to the customer (with retry)
        _send_license_email(email, license_key, tier)

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
async def my_license(email: str, request: Request):
    """
    Let a subscriber look up their license key by email.
    Rate-limited to prevent enumeration.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"my-license:{client_ip}", max_requests=5)

    record = get_by_email(email)
    if not record:
        raise HTTPException(status_code=404, detail="No active subscription found for this email")
    if not record["active"]:
        raise HTTPException(status_code=402, detail="Subscription is inactive")
    return {
        "license_key": record["license_key"],
        "tier": record["tier"],
    }


# ---------- Device management ----------

@app.get("/my-devices")
async def my_devices(license_key: str):
    """List all registered devices for a license key."""
    if not validate_license(license_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive license")
    return {"devices": get_devices(license_key), "max": 3}


@app.delete("/my-devices/{device_id}")
async def delete_device(device_id: str, license_key: str):
    """Deregister a device to free up a slot."""
    if not validate_license(license_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive license")
    removed = remove_device(license_key, device_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"removed": True, "devices": get_devices(license_key)}


# ---------- Usage info ----------

@app.get("/my-usage")
async def my_usage(license_key: str):
    """Return current billing period usage summary."""
    if not validate_license(license_key):
        raise HTTPException(status_code=401, detail="Invalid or inactive license")
    return get_usage_summary(license_key)
