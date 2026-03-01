"""
Gemini API client using google-genai (current SDK) + Files API.
Files are uploaded directly to Gemini — no GCS bucket needed.
Files auto-expire after 48 h; we delete immediately after analysis.
"""

import os
import time
import logging

logger = logging.getLogger(__name__)

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_client = None


def configure(api_key: str):
    global _client
    from google import genai
    _client = genai.Client(api_key=api_key)
    logger.info(f"Gemini configured (model={GEMINI_MODEL})")


def _get_client():
    if _client is None:
        raise RuntimeError("Gemini client not configured — GEMINI_API_KEY missing")
    return _client


# Analysis prompt — lives on the server, never sent to clients
_VIDEO_PROMPT = """Analyze this video clip and provide:

1. SCENE DESCRIPTION: A detailed description of what is visually happening, including:
   - Location/setting (indoor/outdoor, specific environment)
   - People present (appearance, actions, emotions)
   - Objects and subjects of interest
   - Camera movement and composition
   - Lighting conditions

2. TIMELINE: A chronological list of key events with approximate timestamps.

3. KEYWORDS: A comma-separated list of 10-15 searchable keywords describing the content.

4. MOOD/TONE: Describe the overall mood, tone, and emotional quality of the footage.

Be specific and factual. Avoid speculation about identity of unknown individuals."""

_IMAGE_PROMPT = """Describe this image in detail:

1. SCENE DESCRIPTION: Subjects, setting, composition, lighting, colors.
2. KEYWORDS: A comma-separated list of 10-15 searchable keywords.
3. MOOD/TONE: Overall mood and emotional quality.

Be specific and factual."""

_DEV_VIDEO_RESPONSE = """\
SCENE DESCRIPTION:
[DEV MODE] Outdoor interview setting. Two subjects seated in a park.
Natural daylight, late afternoon. Camera on tripod, medium shot.

TIMELINE:
0:00 - Subject A begins speaking
0:15 - Cut to reaction shot of Subject B
0:30 - Wide shot of both subjects

KEYWORDS:
interview, outdoor, park, conversation, natural light, two people, medium shot, afternoon

MOOD/TONE:
Warm, conversational, relaxed."""

_DEV_IMAGE_RESPONSE = """\
SCENE DESCRIPTION:
[DEV MODE] Outdoor location, natural lighting, subjects in foreground.

KEYWORDS:
outdoor, natural light, foreground, landscape

MOOD/TONE:
Neutral, documentary."""


def upload_file(path: str, mime_type: str = "video/mp4"):
    """
    Upload a file to Gemini Files API.
    Polls until ACTIVE (videos need processing time).
    Returns the file object (has .name and .uri).
    """
    client = _get_client()
    logger.info(f"Uploading to Gemini Files API: {path} ({mime_type})")

    with open(path, "rb") as f:
        gemini_file = client.files.upload(
            file=f,
            config={"mime_type": mime_type},
        )

    # Poll until the file is ready (videos take a few seconds to process)
    while gemini_file.state.name == "PROCESSING":
        time.sleep(2)
        gemini_file = client.files.get(name=gemini_file.name)

    if gemini_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini file processing failed: {gemini_file.name}")

    logger.info(f"Gemini file ready: {gemini_file.name}")
    return gemini_file


def delete_file(file_name: str):
    """Delete a file from Gemini Files API."""
    try:
        _get_client().files.delete(name=file_name)
        logger.info(f"Deleted Gemini file: {file_name}")
    except Exception as e:
        logger.warning(f"Could not delete Gemini file {file_name}: {e}")


def analyze(gemini_file, file_type: str = "video") -> str:
    """Run Gemini analysis on an already-uploaded file."""
    if DEV_MODE:
        logger.info("[DEV] Returning mock analysis")
        return _DEV_VIDEO_RESPONSE if file_type != "image" else _DEV_IMAGE_RESPONSE

    client = _get_client()
    prompt = _VIDEO_PROMPT if file_type != "image" else _IMAGE_PROMPT
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[gemini_file, prompt],
    )
    return response.text
