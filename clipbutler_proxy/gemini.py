"""
Vertex AI Gemini client — operator key lives here, never on client machines.

Set DEV_MODE=true to return mock analysis without calling Vertex AI.
"""

import os
import logging

logger = logging.getLogger(__name__)

DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_initialized = False

# Analysis prompt lives on the server — never sent to clients
_ANALYSIS_PROMPT = """Analyze this video clip and provide:

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

_IMAGE_PROMPT = "Describe this image in detail, including subjects, setting, mood, and any text visible."

_DEV_VIDEO_RESPONSE = """\
SCENE DESCRIPTION:
[DEV MODE] Outdoor interview setting. Two subjects are seated facing each other in a park.
Natural daylight, late afternoon. Camera on tripod, medium shot. Shallow depth of field.

TIMELINE:
0:00 - Subject A begins speaking
0:15 - Cut to reaction shot of Subject B
0:30 - Both subjects visible in wide shot
0:45 - Close-up on Subject A

KEYWORDS:
interview, outdoor, park, conversation, natural light, two people, medium shot, tripod, afternoon, talking

MOOD/TONE:
Warm, conversational, relaxed. Natural and authentic feel."""

_DEV_IMAGE_RESPONSE = """\
[DEV MODE] Image shows a scenic outdoor location with natural lighting.
Subjects are positioned in the foreground against a blurred background.
The image has good exposure and color balance."""


def _init():
    global _initialized
    if not _initialized:
        if not GCP_PROJECT:
            raise RuntimeError("GCP_PROJECT env var is required")
        import vertexai
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _initialized = True


def analyze_gcs_video(uri: str) -> str:
    if DEV_MODE:
        logger.info(f"[DEV] Mock video analysis for: {uri}")
        return _DEV_VIDEO_RESPONSE

    from vertexai.generative_models import GenerativeModel, Part
    _init()
    model = GenerativeModel(GEMINI_MODEL)
    video_part = Part.from_uri(uri=uri, mime_type="video/mp4")
    response = model.generate_content(
        [video_part, _ANALYSIS_PROMPT],
        request_options={"timeout": 180},
    )
    return response.text


def analyze_gcs_image(uri: str) -> str:
    if DEV_MODE:
        logger.info(f"[DEV] Mock image analysis for: {uri}")
        return _DEV_IMAGE_RESPONSE

    from vertexai.generative_models import GenerativeModel, Part
    _init()
    model = GenerativeModel(GEMINI_MODEL)
    mime = "image/jpeg"
    uri_lower = uri.lower()
    if uri_lower.endswith(".png"):
        mime = "image/png"
    elif uri_lower.endswith((".tif", ".tiff")):
        mime = "image/tiff"
    image_part = Part.from_uri(uri=uri, mime_type=mime)
    response = model.generate_content([image_part, _IMAGE_PROMPT])
    return response.text
