"""
Ingest scanner — walks watch folders and processes new video/image files.
"""

import os
import time
import uuid
import logging
from pathlib import Path
from typing import List, Optional, Callable

from ..db.sqlite_db import SQLiteDB
from ..db.vector_db import VectorDB
from .metadata import extract_metadata
from .proxy import create_proxy
from .ai_analyzer import analyze_video, UsageLimitError
from .transcriber import transcribe_audio
from .face_engine import process_faces, FACE_AVAILABLE
from ..security.hardware import get_fingerprint

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mxf", ".avi", ".r3d", ".braw"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".dng", ".arw", ".cr2"}
ALL_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

MAX_RETRIES = 3


class IngestScanner:
    def __init__(
        self,
        config_manager,
        proxy_folder: str,
        thumbnail_folder: str,
        sqlite_db: SQLiteDB,
        vector_db: VectorDB,
        on_progress: Optional[Callable[[str, str], None]] = None,
    ):
        self._config_manager = config_manager
        self.proxy_folder = proxy_folder
        self.thumbnail_folder = thumbnail_folder
        self.sqlite_db = sqlite_db
        self.vector_db = vector_db
        self.on_progress = on_progress  # callback(video_id, status_message)
        self._device_id = get_fingerprint()
        self._running = False
        self._usage_limit_hit = False
        self._usage_limit_ts: Optional[float] = None  # when usage limit was hit

    @property
    def watch_paths(self) -> List[str]:
        return self._config_manager.get("watch_paths", [])

    @property
    def license_key(self) -> str:
        return self._config_manager.get("license_key", "")

    @property
    def proxy_url(self) -> str:
        return self._config_manager.get("proxy_url", "")

    @property
    def whisper_model_name(self) -> str:
        return self._config_manager.get("whisper_model", "base")

    def _emit(self, video_id: str, msg: str):
        logger.info(f"[{video_id[:8]}] {msg}")
        if self.on_progress:
            try:
                self.on_progress(video_id, msg)
            except Exception:
                pass

    def scan_once(self):
        """Walk all watch paths and queue any new files."""
        proxy_folder_abs = os.path.abspath(self.proxy_folder)
        for root_path in self.watch_paths:
            if not os.path.exists(root_path):
                logger.warning(f"Watch path not found: {root_path}")
                continue

            for dirpath, dirnames, filenames in os.walk(root_path):
                # Skip hidden dirs and our own proxy folder (path prefix match)
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".")
                    and os.path.abspath(os.path.join(dirpath, d)) != proxy_folder_abs
                ]

                for filename in filenames:
                    ext = Path(filename).suffix.lower()
                    if ext not in ALL_EXTENSIONS:
                        continue

                    full_path = os.path.join(dirpath, filename)
                    if full_path.startswith(proxy_folder_abs):
                        continue

                    # Single DB query: only queue if not already known
                    existing = self.sqlite_db.get_video_by_path(full_path)
                    if existing is None:
                        self.sqlite_db.upsert_video({
                            "id": str(uuid.uuid4()),
                            "filename": filename,
                            "filepath": full_path,
                            "status": "PENDING",
                        })
                        logger.info(f"Queued: {filename}")

    def process_pending(self):
        """Process all PENDING/FAILED videos up to MAX_RETRIES."""
        if not self.license_key:
            logger.debug("No license key configured — AI analysis disabled.")
            return

        # Auto-reset usage limit flag after 1 hour
        if self._usage_limit_hit:
            if self._usage_limit_ts and (time.time() - self._usage_limit_ts) > 3600:
                logger.info("Usage limit hold expired — retrying")
                self._usage_limit_hit = False
                self._usage_limit_ts = None
            else:
                return

        pending = self.sqlite_db.get_pending(max_retries=MAX_RETRIES)
        for record in pending:
            video_id = record["id"]
            filepath = record["filepath"]
            filename = record["filename"]

            if not os.path.exists(filepath):
                self.sqlite_db.set_status(
                    video_id, "FAILED", "File no longer exists"
                )
                continue

            self.sqlite_db.set_status(video_id, "PROCESSING")
            self._emit(video_id, f"Processing: {filename}")

            proxy_path = None
            try:
                # 1. Extract technical + camera metadata
                self._emit(video_id, "Extracting metadata...")
                meta = extract_metadata(filepath)
                meta["id"] = video_id
                meta["filepath"] = filepath
                meta["filename"] = filename
                meta["status"] = "PROCESSING"
                self.sqlite_db.upsert_video(meta)

                ext = Path(filename).suffix.lower()
                description = ""
                transcript = ""

                if ext in IMAGE_EXTENSIONS:
                    # Image: upload directly (no proxy needed)
                    self._emit(video_id, "Analyzing image with AI...")
                    description = analyze_video(
                        proxy_path=filepath,
                        proxy_url=self.proxy_url,
                        license_key=self.license_key,
                        duration_sec=0.0,
                        device_id=self._device_id,
                    )

                else:
                    # Video: proxy → AI analysis → transcription → face detection
                    self._emit(video_id, "Creating proxy...")
                    proxy_path = create_proxy(filepath, self.proxy_folder)

                    self._emit(video_id, "Analyzing with AI Vision...")
                    duration_sec = meta.get("duration_sec", 0.0) or 0.0
                    visual_desc = analyze_video(
                        proxy_path=proxy_path,
                        proxy_url=self.proxy_url,
                        license_key=self.license_key,
                        duration_sec=duration_sec,
                        device_id=self._device_id,
                    )

                    self._emit(video_id, "Transcribing audio...")
                    transcript = transcribe_audio(filepath, self.whisper_model_name)

                    description = f"VISUALS:\n{visual_desc}"
                    if transcript:
                        description += f"\n\nSPEECH:\n{transcript}"

                    # Face detection
                    if FACE_AVAILABLE:
                        self._emit(video_id, "Detecting faces...")
                        face_count = process_faces(
                            video_id=video_id,
                            proxy_path=proxy_path,
                            thumbnail_dir=self.thumbnail_folder,
                            vector_db=self.vector_db,
                            sqlite_db=self.sqlite_db,
                        )
                        if face_count > 0:
                            self._emit(video_id, f"Found {face_count} faces")

                # 2. Store in databases
                self.sqlite_db.upsert_video({
                    "id": video_id,
                    "filepath": filepath,
                    "filename": filename,
                    "ai_description": description,
                    "audio_transcript": transcript,
                    "status": "INDEXED",
                })

                self.vector_db.add_scene(
                    video_id=video_id,
                    description=description,
                    metadata={"filename": filename, "filepath": filepath},
                )

                self._emit(video_id, "INDEXED")
                logger.info(f"Indexed: {filename}")

            except UsageLimitError as e:
                logger.warning(f"Usage limit reached: {e}")
                self.sqlite_db.set_status(video_id, "PENDING", str(e)[:1000])
                self._emit(video_id, f"USAGE_LIMIT: {e}")
                self._usage_limit_hit = True
                self._usage_limit_ts = time.time()
                break  # stop processing remaining queue

            except Exception as e:
                logger.error(f"Failed to process {filename}: {e}", exc_info=True)
                self.sqlite_db.increment_retry(video_id)
                self.sqlite_db.set_status(video_id, "FAILED", str(e)[:1000])
                self._emit(video_id, f"FAILED: {e}")

            finally:
                # Always clean up the proxy
                if proxy_path and os.path.exists(proxy_path):
                    try:
                        os.remove(proxy_path)
                    except OSError:
                        pass

    def run_loop(self, interval_sec: float = 10.0):
        """Main ingest loop. Runs until stop() is called."""
        self._running = True
        logger.info("Ingest scanner started")
        while self._running:
            try:
                self.scan_once()
                self.process_pending()
            except Exception as e:
                logger.error(f"Scanner loop error: {e}", exc_info=True)
            time.sleep(interval_sec)
        logger.info("Ingest scanner stopped")

    def stop(self):
        self._running = False
