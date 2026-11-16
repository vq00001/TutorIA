"""El worker conserva texto y offsets antes de publicar en PostgreSQL."""

from backend.config import Config
from backend import document_worker


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


def test_model_revision_accepts_ollama_latest_tag(monkeypatch):
    config = Config(ollama_url="http://ollama:11434", model_embed="all-minilm", model_llm="qwen3:1.7b")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "all-minilm:latest", "digest": "sha256:actual"}]}

    monkeypatch.setattr(document_worker.requests, "get", lambda *_args, **_kwargs: Response())
    assert document_worker.model_revision(config) == "sha256:actual"
