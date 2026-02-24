"""
Facial recognition pipeline using InsightFace (Buffalo_SC model).
Detects faces in proxy frames, clusters them across clips,
and stores embeddings in ChromaDB.
"""

import os
import uuid
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import cv2
    import insightface
    from insightface.app import FaceAnalysis
    FACE_AVAILABLE = True
except ImportError:
    FACE_AVAILABLE = False


_face_app: Optional[Any] = None
SIMILARITY_THRESHOLD = 0.75


def get_face_app() -> Optional[Any]:
    """Lazy-load the InsightFace model."""
    global _face_app
    if not FACE_AVAILABLE:
        return None
    if _face_app is None:
        _face_app = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app


def extract_faces_from_proxy(
    proxy_path: str,
    thumbnail_dir: str,
    sample_interval_sec: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Sample frames from proxy at sample_interval_sec intervals.
    Detect faces in each frame.
    Returns list of dicts with keys: embedding, thumbnail_path, timestamp_sec, confidence.
    """
    app = get_face_app()
    if app is None or not FACE_AVAILABLE:
        return []

    Path(thumbnail_dir).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(proxy_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    frame_interval = max(1, int(fps * sample_interval_sec))
    frame_count = 0
    detected_faces = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_count % frame_interval == 0:
            timestamp_sec = frame_count / fps
            faces = app.get(frame)

            for face in faces:
                if face.embedding is None:
                    continue
                if face.det_score < 0.5:
                    continue

                # Save thumbnail
                bbox = face.bbox.astype(int)
                x1, y1, x2, y2 = bbox
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)

                thumb_id = uuid.uuid4().hex[:8]
                thumb_path = os.path.join(thumbnail_dir, f"face_{thumb_id}.jpg")
                face_crop = frame[y1:y2, x1:x2]
                if face_crop.size > 0:
                    cv2.imwrite(thumb_path, face_crop)
                else:
                    thumb_path = None

                detected_faces.append({
                    "embedding": face.embedding.tolist(),
                    "thumbnail_path": thumb_path,
                    "timestamp_sec": timestamp_sec,
                    "confidence": float(face.det_score),
                })

        frame_count += 1

    cap.release()
    return detected_faces


def process_faces(
    video_id: str,
    proxy_path: str,
    thumbnail_dir: str,
    vector_db: Any,
    sqlite_db: Any,
) -> int:
    """
    Full face processing pipeline for one video.
    Returns the number of faces stored.
    """
    if not FACE_AVAILABLE:
        return 0

    detected = extract_faces_from_proxy(proxy_path, thumbnail_dir)
    stored = 0

    for face_data in detected:
        embedding = face_data["embedding"]

        # Check if this face matches an existing cluster
        similar = vector_db.find_similar_faces(
            embedding, n=5, threshold=SIMILARITY_THRESHOLD
        )

        if similar:
            # Use the cluster_id from the most similar known face
            best_face_id, similarity, meta = similar[0]
            cluster_id = meta.get("cluster_id", best_face_id)
        else:
            # New face, create a new cluster
            cluster_id = str(uuid.uuid4())

        face_id = str(uuid.uuid4())

        # Store embedding in ChromaDB
        vector_db.add_face_embedding(
            face_id=face_id,
            embedding=embedding,
            metadata={"cluster_id": cluster_id, "video_id": video_id},
        )

        # Store record in SQLite
        sqlite_db.add_face({
            "id": face_id,
            "video_id": video_id,
            "cluster_id": cluster_id,
            "confidence": face_data["confidence"],
            "timestamp_sec": face_data["timestamp_sec"],
            "thumbnail_path": face_data["thumbnail_path"],
        })

        stored += 1

    return stored
