"""Modelos de datos que conectan chunking, embeddings y almacenamiento."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Section:
    """Sección textual con su ruta jerárquica dentro del documento."""

    path: list[str]
    body: str


@dataclass
class Parent:
    """Fragmento grande que se devuelve al LLM como contexto completo."""

    id: str
    doc_id: str
    index: int
    path: list[str]
    text: str


@dataclass
class Child:
    """Fragmento pequeño que se indexa semánticamente en el almacén vectorial."""

    id: str
    parent_id: str
    doc_id: str
    index: int
    path: list[str]
    text: str
    embed_text: str
