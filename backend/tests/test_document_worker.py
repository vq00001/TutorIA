"""El worker conserva texto y offsets antes de publicar en PostgreSQL."""

import pymupdf
import pytest

from backend import document_worker
from backend.config import Config
from backend.utilities.pdf_to_md.pdf_to_md import NotConvertible


def test_chunks_for_publication_keep_source_offsets(monkeypatch):
    config = Config(ollama_url="http://ollama:11434", model_embed="all-minilm", model_llm="qwen3:1.7b")
    text = "# Integrales\n\nUna integral doble calcula áreas.\n\nFubini permite integrar por etapas."
    monkeypatch.setattr(document_worker, "embed", lambda _config, texts, batch_size: [[1.0, 0.0] for _ in texts])
    extracted_text, parents, children = document_worker.chunks_for_publication(text, "guia.md", config)
    assert parents and children
    for child in children:
        assert extracted_text[child["start_offset"]:child["end_offset"]] == child["text"]
        assert child["embedding"] == "[1.0,0.0]"
        assert 0 <= child["parent_index"] < len(parents)


def test_extract_document_text_converts_and_validates_pdf():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Este documento PDF contiene texto suficiente para su validacion.")
    pdf_bytes = doc.tobytes()
    doc.close()

    text = document_worker.extract_document_text("GUIA.PDF", pdf_bytes)

    assert "Este documento PDF contiene texto suficiente para su validacion." in text
    with pytest.raises(NotConvertible, match="PDF"):
        document_worker.extract_document_text("guia.pdf", b"%PDF-archivo incompleto")


def test_extract_document_text_decodes_utf8_documents():
    assert document_worker.extract_document_text("guia.md", b"Apuntes") == "Apuntes"


def test_model_revision_accepts_ollama_latest_tag(monkeypatch):
    config = Config(ollama_url="http://ollama:11434", model_embed="all-minilm", model_llm="qwen3:1.7b")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "all-minilm:latest", "digest": "sha256:actual"}]}

    monkeypatch.setattr(document_worker.requests, "get", lambda *_args, **_kwargs: Response())
    assert document_worker.model_revision(config) == "sha256:actual"
