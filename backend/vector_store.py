"""Persistencia parent/child y búsqueda vectorial sobre Qdrant."""

from __future__ import annotations

from typing import Any

import requests

from .config import Config
from .models import Child, Parent


class QdrantStore:
    """Persistencia parent/child y búsqueda vectorial sobre Qdrant."""

    def __init__(self, config: Config) -> None:
        """Crea un almacén conectado mediante la configuración recibida."""
        self.config = config

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Ejecuta una petición REST y convierte su respuesta a un diccionario."""
        response = requests.request(
            method, f"{self.config.qdrant_url}{path}", timeout=60, **kwargs
        )
        response.raise_for_status()
        return response.json()

    def ensure_collections(self, vector_size: int) -> None:
        """Crea colecciones ausentes y valida el tamaño de sus vectores."""
        for name, size in (
            (self.config.collection, vector_size),
            (f"{self.config.collection}_parents", 1),
        ):
            response = requests.get(
                f"{self.config.qdrant_url}/collections/{name}", timeout=30
            )
            if response.status_code == 404:
                self._request(
                    "PUT",
                    f"/collections/{name}",
                    json={"vectors": {"size": size, "distance": "Cosine"}},
                )
            elif response.status_code != 200:
                response.raise_for_status()
            else:
                configured_size = response.json()["result"]["config"]["params"]["vectors"]["size"]
                if configured_size != size:
                    raise ValueError(
                        f"La colección {name!r} usa vectores de tamaño {configured_size}, "
                        f"pero se recibió tamaño {size}"
                    )

    def delete_document(self, doc_id: str) -> None:
        """Elimina la versión anterior de un documento antes de reindexarlo."""
        payload = {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}}
        for name in (self.config.collection, f"{self.config.collection}_parents"):
            self._request(
                "POST", f"/collections/{name}/points/delete?wait=true", json=payload
            )

    def _upsert(self, collection: str, points: list[dict[str, Any]]) -> None:
        """Sube puntos en lotes para evitar requests demasiado grandes."""
        for start in range(0, len(points), 64):
            self._request(
                "PUT",
                f"/collections/{collection}/points?wait=true",
                json={"points": points[start : start + 64]},
            )

    def save(
        self, parents: list[Parent], children: list[Child], vectors: list[list[float]]
    ) -> None:
        """Guarda parents y children; cada child debe tener un vector asociado."""
        if len(children) != len(vectors):
            raise ValueError("Debe existir un vector por cada child")
        self.delete_document(parents[0].doc_id)
        self._upsert(
            f"{self.config.collection}_parents",
            [
                {
                    "id": parent.id,
                    "vector": [1.0],
                    "payload": {
                        "doc_id": parent.doc_id,
                        "index": parent.index,
                        "path": parent.path,
                        "text": parent.text,
                    },
                }
                for parent in parents
            ],
        )
        self._upsert(
            self.config.collection,
            [
                {
                    "id": child.id,
                    "vector": vector,
                    "payload": {
                        "doc_id": child.doc_id,
                        "parent_id": child.parent_id,
                        "index": child.index,
                        "path": child.path,
                        "text": child.text,
                    },
                }
                for child, vector in zip(children, vectors)
            ],
        )

    def search(self, vector: list[float], limit: int) -> list[dict[str, Any]]:
        """Busca children y devuelve parents únicos ordenados por mejor score."""
        result = self._request(
            "POST",
            f"/collections/{self.config.collection}/points/search",
            json={"vector": vector, "limit": limit * 4, "with_payload": True},
        ).get("result", [])
        best: dict[str, float] = {}
        for hit in result:
            payload = hit.get("payload", {})
            parent_id = payload.get("parent_id")
            if parent_id:
                # Qdrant devuelve varios children del mismo parent; conservar
                # el primer score mantiene el mejor resultado de cada parent.
                best.setdefault(parent_id, float(hit["score"]))
        parent_ids = list(best)[:limit]
        if not parent_ids:
            return []
        parents = self._request(
            "POST",
            f"/collections/{self.config.collection}_parents/points",
            json={"ids": parent_ids, "with_payload": True},
        ).get("result", [])
        by_id = {parent["id"]: parent.get("payload", {}) for parent in parents}
        return [
            {**by_id[parent_id], "score": best[parent_id]}
            for parent_id in parent_ids
            if parent_id in by_id
        ]
