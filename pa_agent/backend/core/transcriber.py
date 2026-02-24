"""
Audio transcription via OpenAI Whisper (local model).
Whisper is optional — set WHISPER_AVAILABLE to check before calling.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import whisper as _whisper
    WHISPER_AVAILABLE = True
except ImportError:
    _whisper = None
    WHISPER_AVAILABLE = False
    logger.info("Whisper not installed — transcription disabled. pip install openai-whisper to enable.")

_audio_model = None


def get_audio_model(model_name: str = "base"):
    global _audio_model
    if _audio_model is None:
        _audio_model = _whisper.load_model(model_name)
    return _audio_model


def transcribe_audio(video_path: str, model_name: str = "base") -> str:
    """
    Transcribe audio from a video file.
    Returns formatted transcript with timestamps, or empty string if
    Whisper is not installed or no speech is detected.
    """
    if not WHISPER_AVAILABLE:
        return ""

    try:
        model = get_audio_model(model_name)
        result = model.transcribe(video_path, verbose=False)
    except Exception as e:
        logger.warning(f"Transcription failed for {video_path}: {e}")
        return ""

    segments = result.get("segments", [])
    if not segments:
        return ""

    lines = []
    for seg in segments:
        start = time.strftime("%H:%M:%S", time.gmtime(seg["start"]))
        end = time.strftime("%H:%M:%S", time.gmtime(seg["end"]))
        text = seg["text"].strip()
        if text:
            lines.append(f"[{start} - {end}] {text}")

    return "\n".join(lines)
