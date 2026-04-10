"""
SQLite database operations for ClipButler.
All video metadata, status tracking, and face records.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


class SQLiteDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    file_hash TEXT,
                    file_size_bytes INTEGER,
                    -- Technical metadata (from FFprobe)
                    duration_sec REAL,
                    fps REAL,
                    resolution_w INTEGER,
                    resolution_h INTEGER,
                    video_codec TEXT,
                    audio_codec TEXT,
                    bitrate_kbps INTEGER,
                    -- Camera metadata (from ExifTool)
                    camera_make TEXT,
                    camera_model TEXT,
                    -- Timestamps
                    date_recorded TEXT,
                    date_indexed TEXT,
                    -- AI results
                    ai_description TEXT,
                    audio_transcript TEXT,
                    -- Processing state
                    status TEXT DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    error_log TEXT,
                    -- Organization
                    project_name TEXT,
                    tags TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_filepath
                    ON videos(filepath);

                CREATE INDEX IF NOT EXISTS idx_videos_status
                    ON videos(status);

                CREATE INDEX IF NOT EXISTS idx_videos_file_hash
                    ON videos(file_hash);

                CREATE TABLE IF NOT EXISTS faces (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    cluster_id TEXT,
                    identity_label TEXT,
                    confidence REAL,
                    timestamp_sec REAL,
                    thumbnail_path TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_faces_video_id
                    ON faces(video_id);

                CREATE INDEX IF NOT EXISTS idx_faces_cluster_id
                    ON faces(cluster_id);
            """)
        finally:
            conn.close()

    # ---- Video operations ----

    def upsert_video(self, video_data: Dict[str, Any]) -> str:
        """Insert or update a video record. Returns the video id."""
        if "id" not in video_data:
            video_data["id"] = str(uuid.uuid4())
        if "date_indexed" not in video_data:
            video_data["date_indexed"] = datetime.utcnow().isoformat()
        if "status" not in video_data:
            video_data["status"] = "PENDING"

        columns = list(video_data.keys())
        placeholders = ", ".join(["?" for _ in columns])
        updates = ", ".join([f"{c}=excluded.{c}" for c in columns if c != "id"])

        sql = f"""
            INSERT INTO videos ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(filepath) DO UPDATE SET {updates}
        """
        conn = self._get_conn()
        try:
            conn.execute(sql, list(video_data.values()))
            conn.commit()
        finally:
            conn.close()
        return video_data["id"]

    def get_video(self, video_id: str) -> Optional[Dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_video_by_path(self, filepath: str) -> Optional[Dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM videos WHERE filepath = ?", (filepath,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def is_processed(self, filepath: str) -> bool:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM videos WHERE filepath = ? AND status = 'INDEXED'",
                (filepath,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def set_status(self, video_id: str, status: str, error_log: str = None):
        conn = self._get_conn()
        try:
            if error_log:
                conn.execute(
                    "UPDATE videos SET status=?, error_log=? WHERE id=?",
                    (status, error_log, video_id)
                )
            else:
                conn.execute(
                    "UPDATE videos SET status=? WHERE id=?",
                    (status, video_id)
                )
            conn.commit()
        finally:
            conn.close()

    def increment_retry(self, video_id: str):
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE videos SET retry_count = retry_count + 1 WHERE id=?",
                (video_id,)
            )
            conn.commit()
        finally:
            conn.close()

    def reset_retry(self, video_id: str):
        """Reset retry count to 0 so the file can be re-queued."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE videos SET retry_count = 0 WHERE id=?",
                (video_id,)
            )
            conn.commit()
        finally:
            conn.close()

    def recover_stuck_processing(self):
        """Reset files stuck in PROCESSING back to PENDING (e.g., after crash)."""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE videos SET status='PENDING' WHERE status='PROCESSING'"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def get_pending(self, max_retries: int = 3) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM videos WHERE status IN ('PENDING','FAILED') AND retry_count < ?",
                (max_retries,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def search(
        self,
        ids: Optional[List[str]] = None,
        fps: Optional[float] = None,
        resolution: Optional[str] = None,
        camera: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        duration_min: Optional[float] = None,
        duration_max: Optional[float] = None,
        keyword: Optional[str] = None,
        n: int = 20,
    ) -> List[Dict]:
        conditions = ["status = 'INDEXED'"]
        params: List[Any] = []

        if ids:
            placeholders = ",".join(["?" for _ in ids])
            conditions.append(f"id IN ({placeholders})")
            params.extend(ids)

        if fps is not None:
            conditions.append("ABS(fps - ?) < 0.1")
            params.append(fps)

        if resolution:
            res_map = {"4k": 3840, "2k": 2048, "1080p": 1920, "720p": 1280}
            min_w = res_map.get(resolution.lower())
            if min_w:
                conditions.append("resolution_w >= ?")
                params.append(min_w)

        if camera:
            conditions.append("(camera_make LIKE ? OR camera_model LIKE ?)")
            params.extend([f"%{camera}%", f"%{camera}%"])

        if date_from:
            conditions.append("date_recorded >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("date_recorded <= ?")
            params.append(date_to)

        if duration_min is not None:
            conditions.append("duration_sec >= ?")
            params.append(duration_min)

        if duration_max is not None:
            conditions.append("duration_sec <= ?")
            params.append(duration_max)

        if keyword:
            conditions.append(
                "(ai_description LIKE ? OR audio_transcript LIKE ? OR filename LIKE ?)"
            )
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM videos WHERE {where} LIMIT ?"
        params.append(n)

        conn = self._get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            indexed = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status='INDEXED'"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status IN ('PENDING','PROCESSING')"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status='FAILED'"
            ).fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE date_indexed LIKE ?",
                (f"{datetime.utcnow().date()}%",)
            ).fetchone()[0]
            return {
                "total": total,
                "indexed": indexed,
                "pending": pending,
                "failed": failed,
                "processed_today": today,
            }
        finally:
            conn.close()

    # ---- Face operations ----

    def add_face(self, face_data: Dict[str, Any]) -> str:
        if "id" not in face_data:
            face_data["id"] = str(uuid.uuid4())
        columns = list(face_data.keys())
        placeholders = ", ".join(["?" for _ in columns])
        sql = f"INSERT OR IGNORE INTO faces ({', '.join(columns)}) VALUES ({placeholders})"
        conn = self._get_conn()
        try:
            conn.execute(sql, list(face_data.values()))
            conn.commit()
        finally:
            conn.close()
        return face_data["id"]

    def get_faces_for_video(self, video_id: str) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM faces WHERE video_id=?", (video_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_face_clusters(self) -> List[Dict]:
        """Return unique clusters with their labels and a representative thumbnail."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT cluster_id, identity_label, thumbnail_path,
                       COUNT(*) as appearance_count
                FROM faces
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id
                ORDER BY appearance_count DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def label_face_cluster(self, cluster_id: str, name: str):
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE faces SET identity_label=? WHERE cluster_id=?",
                (name, cluster_id)
            )
            conn.commit()
        finally:
            conn.close()

    def get_video_ids_for_cluster(self, cluster_id: str) -> List[str]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT video_id FROM faces WHERE cluster_id=?",
                (cluster_id,)
            ).fetchall()
            return [r["video_id"] for r in rows]
        finally:
            conn.close()
