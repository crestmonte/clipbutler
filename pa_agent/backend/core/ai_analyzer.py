"""
AI video/image analysis via ClipButler Proxy Service.
Proxy handles Gemini API key and analysis prompt — neither is stored on the client.
"""

import time
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class UsageLimitError(Exception):
    """Raised when the monthly usage limit has been reached."""
    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("message", "Monthly usage limit reached"))


def analyze_video(
    proxy_path: str,
    proxy_url: str,
    license_key: str,
    duration_sec: float = 0.0,
    max_retries: int = 3,
    device_id: str = "",
) -> str:
    """
    Upload a local proxy file to GCS via a presigned URL, then request analysis
    from the ClipButler proxy service.

    Steps:
      1. POST /session  → presigned GCS upload URL + session_id
      2. PUT {upload_url} → upload file directly to GCS (bypasses app server)
      3. POST /analyze  → proxy calls Gemini, returns description text

    Returns the description string.
    """
    # Step 1: create session and get presigned upload URL
    try:
        resp = requests.post(
            f"{proxy_url}/session",
            json={"license_key": license_key, "device_id": device_id},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = _extract_detail(e.response)
        raise RuntimeError(f"Session creation failed ({e.response.status_code}): {detail}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Cannot reach proxy service at {proxy_url}: {e}") from e

    session = resp.json()
    session_id = session["session_id"]
    upload_url = session["upload_url"]

    # Step 2: upload proxy file directly to GCS
    ext = Path(proxy_path).suffix.lower()
    file_type = "image" if ext in {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".dng", ".arw", ".cr2"} else "video"
    content_type = "image/jpeg" if file_type == "image" else "video/mp4"

    try:
        with open(proxy_path, "rb") as f:
            up = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": content_type},
                timeout=300,
            )
        up.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"GCS upload failed ({e.response.status_code})") from e
    except requests.RequestException as e:
        raise RuntimeError(f"GCS upload error: {e}") from e

    # Step 3: request analysis (retry on 5xx)
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.post(
                f"{proxy_url}/analyze",
                json={
                    "session_id": session_id,
                    "duration_sec": duration_sec,
                    "filename": Path(proxy_path).name,
                    "file_type": file_type,
                },
                headers={"Authorization": f"Bearer {license_key}"},
                timeout=180,
            )
            if r.status_code == 200:
                return r.json()["description"]
            if r.status_code == 429:
                # Usage limit reached — raise distinct error, don't retry
                detail = r.json().get("detail", {})
                if isinstance(detail, str):
                    detail = {"message": detail}
                raise UsageLimitError(detail)
            if r.status_code in (401, 402, 403):
                raise RuntimeError(f"Proxy error {r.status_code}: {_extract_detail(r)}")
            # 4xx other than auth — don't retry
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"Proxy error {r.status_code}: {_extract_detail(r)}")
            # 5xx — retry
            last_error = RuntimeError(f"Proxy server error {r.status_code}: {_extract_detail(r)}")
        except RuntimeError:
            raise
        except requests.RequestException as e:
            last_error = e

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    raise RuntimeError(f"Analysis failed after {max_retries} attempts: {last_error}")


def _extract_detail(response) -> str:
    try:
        return response.json().get("detail", response.text[:200])
    except Exception:
        return response.text[:200] if hasattr(response, "text") else str(response)
