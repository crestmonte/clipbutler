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
        watch_paths: List[str],
        proxy_folder: str,
        thumbnail_folder: str,
        sqlite_db: SQLiteDB,
        vector_db: VectorDB,
        proxy_url: str,
        license_key: str,
        whisper_model_name: str = "base",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ):
        self.watch_paths = watch_paths
        self.proxy_folder = proxy_folder
        self.thumbnail_folder = thumbnail_folder
        self.sqlite_db = sqlite_db
        self.vector_db = vector_db
        self.proxy_url = proxy_url
        self.license_key = license_key
        self.whisper_model_name = whisper_model_name
        self.on_progress = on_progress  # callback(video_id, status_message)
        self._device_id = get_fingerprint()
        self._running = False
        self._usage_limit_hit = False

    def _emit(self, video_id: str, msg: str):
        logger.info(f"[{video_id[:8]}] {msg}")
        if self.on_progress:
            try:
                self.on_progress(video_id, msg)
            except Exception:
                pass

    def scan_once(self):
        """Walk all watch paths and queue any new files."""
        for root_path in self.watch_paths:
            if not os.path.exists(root_path):
                logger.warning(f"Watch path not found: {root_path}")
                continue

            for dirpath, dirnames, filenames in os.walk(root_path):
                # Skip hidden dirs and our own proxy folder
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".")
                    and os.path.join(dirpath, d) != self.proxy_folder
                ]

                for filename in filenames:
                    ext = Path(filename).suffix.lower()
                    if ext not in ALL_EXTENSIONS:
                        continue

                    full_path = os.path.join(dirpath, filename)
                    if self.proxy_folder in full_path:
                        continue

                    # Only queue if not already indexed
                    if not self.sqlite_db.is_processed(full_path):
                        existing = self.sqlite_db.get_video_by_path(full_path)
                        if existing is None:
                            # Register as PENDING
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
            logger.warning("No license key configured — AI analysis disabled. Add a license key in Settings.")
            return

        if self._usage_limit_hit:
            return  # stop processing until next billing period or restart

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
                self.sqlite_db.set_status(video_id, "USAGE_LIMIT", str(e)[:1000])
                self._emit(video_id, f"USAGE_LIMIT: {e}")
                self._usage_limit_hit = True
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
