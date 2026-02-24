"""
ChromaDB vector store operations for ClipButler.
Two collections: video_scenes (semantic) and faces (embeddings).
"""

import chromadb
from chromadb.config import Settings
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any


class VectorDB:
    def __init__(self, chroma_path: str):
        Path(chroma_path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=chroma_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self.scenes = self.client.get_or_create_collection(
            name="video_scenes",
            metadata={"hnsw:space": "cosine"},
        )
        self.faces = self.client.get_or_create_collection(
            name="face_embeddings",
            metadata={"hnsw:space": "cosine"},
        )

    # ---- Scene (text) operations ----

    def add_scene(self, video_id: str, description: str, metadata: Dict = None):
        """Add or update a video scene description."""
        self.scenes.upsert(
            ids=[video_id],
            documents=[description],
            metadatas=[metadata or {}],
        )

    def search_scenes(self, query: str, n: int = 50) -> List[Tuple[str, float]]:
        """Returns list of (video_id, distance) sorted by relevance."""
        if self.scenes.count() == 0:
            return []
        results = self.scenes.query(
            query_texts=[query],
            n_results=min(n, self.scenes.count()),
        )
        if not results["ids"] or not results["ids"][0]:
            return []
        return list(zip(results["ids"][0], results["distances"][0]))

    def delete_scene(self, video_id: str):
        try:
            self.scenes.delete(ids=[video_id])
        except Exception:
            pass

    # ---- Face embedding operations ----

    def add_face_embedding(
        self,
        face_id: str,
        embedding: List[float],
        metadata: Dict = None,
    ):
        self.faces.upsert(
            ids=[face_id],
            embeddings=[embedding],
            metadatas=[metadata or {}],
        )

    def find_similar_faces(
        self, embedding: List[float], n: int = 5, threshold: float = 0.75
    ) -> List[Tuple[str, float, Dict]]:
        """
        Returns list of (face_id, similarity_score, metadata) for faces
        within the similarity threshold.
        """
        if self.faces.count() == 0:
            return []
        results = self.faces.query(
            query_embeddings=[embedding],
            n_results=min(n, self.faces.count()),
        )
        if not results["ids"] or not results["ids"][0]:
            return []

        matches = []
        for face_id, dist, meta in zip(
            results["ids"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            similarity = 1.0 - dist  # cosine distance → similarity
            if similarity >= threshold:
                matches.append((face_id, similarity, meta))
        return matches

    def delete_face(self, face_id: str):
        try:
            self.faces.delete(ids=[face_id])
        except Exception:
            pass

    def get_scene_count(self) -> int:
        return self.scenes.count()

    def get_face_count(self) -> int:
        return self.faces.count()
