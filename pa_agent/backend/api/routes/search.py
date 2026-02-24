"""
Search API routes.
GET /api/search — hybrid semantic + structured metadata search.
"""

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


def _format_result(row: Dict[str, Any], faces: List[Dict]) -> Dict[str, Any]:
    """Format a DB row into the API result shape."""
    w = row.get("resolution_w")
    h = row.get("resolution_h")
    resolution = f"{w}x{h}" if w and h else None

    face_labels = []
    for f in faces:
        label = f.get("identity_label") or f"Unknown #{f.get('cluster_id', '')[:4]}"
        if label not in face_labels:
            face_labels.append(label)

    return {
        "id": row["id"],
        "filename": row.get("filename"),
        "filepath": row.get("filepath"),
        "description": row.get("ai_description", ""),
        "transcript": row.get("audio_transcript", ""),
        "fps": row.get("fps"),
        "resolution": resolution,
        "duration_sec": row.get("duration_sec"),
        "video_codec": row.get("video_codec"),
        "audio_codec": row.get("audio_codec"),
        "camera_make": row.get("camera_make"),
        "camera_model": row.get("camera_model"),
        "date_recorded": row.get("date_recorded"),
        "date_indexed": row.get("date_indexed"),
        "project_name": row.get("project_name"),
        "tags": row.get("tags"),
        "faces": face_labels,
        "thumbnail": f"/api/thumbnail/{row['id']}",
        "status": row.get("status"),
    }


def make_search_router(sqlite_db, vector_db):
    """Factory: create the search router with injected DB instances."""

    @router.get("")
    async def search(
        q: Optional[str] = Query(None, description="Semantic search query"),
        fps: Optional[float] = Query(None),
        resolution: Optional[str] = Query(None, description="4k, 2k, 1080p, 720p"),
        camera: Optional[str] = Query(None, description="Camera make or model substring"),
        date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
        date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
        duration_min: Optional[float] = Query(None),
        duration_max: Optional[float] = Query(None),
        n: int = Query(20, ge=1, le=100),
    ):
        candidate_ids: Optional[List[str]] = None

        # Step 1: Semantic search via ChromaDB
        if q:
            semantic_hits = vector_db.search_scenes(q, n=50)
            candidate_ids = [vid_id for vid_id, _dist in semantic_hits]

        # Step 2: Also check face identity labels if q is provided
        if q:
            face_clusters = sqlite_db.get_face_clusters()
            for cluster in face_clusters:
                label = cluster.get("identity_label") or ""
                if q.lower() in label.lower():
                    video_ids = sqlite_db.get_video_ids_for_cluster(
                        cluster["cluster_id"]
                    )
                    if candidate_ids is None:
                        candidate_ids = video_ids
                    else:
                        # merge, deduplicate, preserve semantic order
                        existing = set(candidate_ids)
                        candidate_ids.extend(
                            [vid for vid in video_ids if vid not in existing]
                        )

        # Step 3: SQLite structured filter
        rows = sqlite_db.search(
            ids=candidate_ids,
            fps=fps,
            resolution=resolution,
            camera=camera,
            date_from=date_from,
            date_to=date_to,
            duration_min=duration_min,
            duration_max=duration_max,
            keyword=q if candidate_ids is None else None,  # fallback full-text
            n=n,
        )

        # Step 4: Attach face data + format results
        results = []
        for row in rows:
            faces = sqlite_db.get_faces_for_video(row["id"])
            results.append(_format_result(row, faces))

        # Preserve semantic ranking order if we have candidate_ids
        if candidate_ids:
            id_order = {vid_id: i for i, vid_id in enumerate(candidate_ids)}
            results.sort(key=lambda r: id_order.get(r["id"], 9999))

        return {"results": results, "total": len(results)}

    return router
