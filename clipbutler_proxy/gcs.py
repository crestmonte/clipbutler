"""
Google Cloud Storage presigned URL generation and object lifecycle.

Set DEV_MODE=true to use local /tmp storage instead of real GCS.
In dev mode, upload URLs point to the local server's PUT /dev-upload/{token} endpoint.
"""

import os
import uuid
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
DEV_DIR = Path("/tmp/clipbutler_dev")
DEV_HOST = os.environ.get("DEV_HOST", "http://localhost:8000")

GCS_BUCKET = os.environ.get("GCS_BUCKET", "clipbutler-proxies")


# ---- Production (real GCS) helpers ----

def _get_gcs_client():
    from google.cloud import storage
    return storage.Client()


# ---- Public API ----

def generate_upload_url(expiry_seconds: int = 600) -> tuple[str, str]:
    """
    Returns (object_name, upload_url).
    In dev mode: saves to /tmp; URL points to local PUT endpoint.
    In prod: GCS presigned PUT URL.
    """
    token = str(uuid.uuid4())

    if DEV_MODE:
        DEV_DIR.mkdir(parents=True, exist_ok=True)
        object_name = token
        upload_url = f"{DEV_HOST}/dev-upload/{token}"
        logger.debug(f"[DEV] upload URL: {upload_url}")
        return object_name, upload_url

    from datetime import timedelta
    client = _get_gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    object_name = f"uploads/{token}.mp4"
    blob = bucket.blob(object_name)
    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiry_seconds),
        method="PUT",
        content_type="video/mp4",
    )
    return object_name, url


def object_exists(object_name: str) -> bool:
    if DEV_MODE:
        return (DEV_DIR / object_name).exists()
    try:
        client = _get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        return bucket.blob(object_name).exists()
    except Exception as e:
        logger.error(f"GCS exists check failed for {object_name}: {e}")
        return False


def delete_object(object_name: str):
    if DEV_MODE:
        path = DEV_DIR / object_name
        try:
            path.unlink(missing_ok=True)
            logger.debug(f"[DEV] Deleted local file: {path}")
        except Exception as e:
            logger.warning(f"[DEV] Delete failed for {path}: {e}")
        return
    try:
        client = _get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        bucket.blob(object_name).delete()
        logger.info(f"Deleted GCS object: {object_name}")
    except Exception as e:
        logger.warning(f"Failed to delete GCS object {object_name}: {e}")


def gcs_uri(object_name: str) -> str:
    if DEV_MODE:
        return str(DEV_DIR / object_name)
    return f"gs://{GCS_BUCKET}/{object_name}"
