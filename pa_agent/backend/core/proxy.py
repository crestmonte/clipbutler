"""
Proxy video creation via FFmpeg.
Creates a 480p/5fps H.264 proxy for AI analysis.
"""

import os
import uuid
import subprocess
from pathlib import Path


def create_proxy(input_path: str, proxy_folder: str) -> str:
    """
    Create a low-res proxy for the given video file.
    Returns the proxy file path.
    Raises ValueError if FFmpeg fails.
    """
    Path(proxy_folder).mkdir(parents=True, exist_ok=True)

    filename = Path(input_path).name
    # UUID prefix avoids collisions for identically-named files in different folders
    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    proxy_path = os.path.join(proxy_folder, unique_name)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=480:'trunc(ow/a/2)*2',fps=5",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-an",  # no audio in proxy (separate audio track for Whisper)
        proxy_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        if os.path.exists(proxy_path):
            os.remove(proxy_path)
        raise ValueError(f"FFmpeg proxy creation failed: {result.stderr[-500:]}")

    return proxy_path
