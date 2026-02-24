"""
Technical metadata extraction via FFprobe and ExifTool.
"""

import json
import subprocess
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional


def get_file_hash(filepath: str, chunk_size: int = 65536) -> str:
    """SHA256 hash of the first 64KB of the file (fast fingerprint)."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read(chunk_size))
    return h.hexdigest()


def extract_ffprobe(filepath: str) -> Dict[str, Any]:
    """Run ffprobe and return parsed technical metadata."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return {}

    metadata: Dict[str, Any] = {}

    # Format-level info
    fmt = data.get("format", {})
    metadata["file_size_bytes"] = int(fmt.get("size", 0)) or None
    try:
        metadata["duration_sec"] = float(fmt.get("duration", 0)) or None
    except (ValueError, TypeError):
        metadata["duration_sec"] = None
    try:
        br = fmt.get("bit_rate")
        metadata["bitrate_kbps"] = int(br) // 1000 if br else None
    except (ValueError, TypeError):
        metadata["bitrate_kbps"] = None

    # Extract date from format tags
    tags = fmt.get("tags", {})
    metadata["date_recorded"] = (
        tags.get("creation_time")
        or tags.get("date")
        or tags.get("com.apple.quicktime.creationdate")
    )

    # Stream-level info
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")

        if codec_type == "video" and "video_codec" not in metadata:
            metadata["video_codec"] = stream.get("codec_name")
            metadata["resolution_w"] = stream.get("width")
            metadata["resolution_h"] = stream.get("height")

            # Parse FPS (can be "24000/1001" or "30/1" etc.)
            r_frame_rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den)
                metadata["fps"] = round(fps, 3) if fps > 0 else None
            except (ValueError, ZeroDivisionError):
                metadata["fps"] = None

        elif codec_type == "audio" and "audio_codec" not in metadata:
            metadata["audio_codec"] = stream.get("codec_name")

    return metadata


def extract_exiftool(filepath: str) -> Dict[str, Any]:
    """Run exiftool and return camera make/model."""
    try:
        result = subprocess.run(
            ["exiftool", "-json", "-Make", "-Model", "-DateTimeOriginal",
             "-CreateDate", filepath],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        data = json.loads(result.stdout)
        if not data:
            return {}
        record = data[0]
        out: Dict[str, Any] = {}
        if "Make" in record:
            out["camera_make"] = record["Make"]
        if "Model" in record:
            out["camera_model"] = record["Model"]
        date = record.get("DateTimeOriginal") or record.get("CreateDate")
        if date:
            out["date_recorded"] = date
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


def extract_metadata(filepath: str) -> Dict[str, Any]:
    """
    Full metadata extraction pipeline.
    Returns a dict ready to be merged into the videos table row.
    """
    result: Dict[str, Any] = {}

    # File hash for move/rename detection
    try:
        result["file_hash"] = get_file_hash(filepath)
    except OSError:
        result["file_hash"] = None

    # FFprobe for technical metadata
    ffprobe_data = extract_ffprobe(filepath)
    result.update(ffprobe_data)

    # ExifTool for camera metadata (overrides date if found)
    exif_data = extract_exiftool(filepath)
    result.update(exif_data)

    return result
