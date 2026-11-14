from uuid import uuid4

import pytest

from backend.experiments.knowledge_graph import concepts


def source(ordinal, tokens=1200):
    return dict(
        id=uuid4(),
        text=f"Contenido del fragmento {ordinal}",
        locator={"section": ordinal},
        token_estimate=tokens,
    )


def test_extract_candidates_rejects_source_outside_its_batch(monkeypatch):
    sources = [source(0), source(1)]

    def fake_model_json(_model, _prompt, _batch, _timeout):
        return {
            "concepts": [
                {
                    "label": "Concepto",
                    "description": "Tema",
                    "sourceRefs": ["S2"],
                }
            ]
        }

    monkeypatch.setattr(concepts, "model_json", fake_model_json)
    with pytest.raises(ValueError, match="INVALID_CONCEPT_SOURCE"):
        concepts.extract_candidates(sources, "test-model", lambda: 30)


def test_extract_candidates_maps_reference_to_source_id(monkeypatch):
    sources = [source(0, tokens=500), source(1, tokens=500)]

    def fake_model_json(_model, _prompt, batch, _timeout):
        assert [item["ref"] for item in batch] == ["S1", "S2"]
        return {
            "concepts": [
                {"label": "Integración", "description": "Tema", "sourceRefs": ["S2"]}
            ]
        }

    monkeypatch.setattr(concepts, "model_json", fake_model_json)
    result = concepts.extract_candidates(sources, "test-model", lambda: 30)

    assert result["integracion"]["source_ids"] == {sources[1]["id"]}


def test_extract_candidates_does_not_send_source_ids_to_model(monkeypatch):
    sources = [source(0)]

    def fake_model_json(_model, prompt, batch, _timeout):
        assert str(sources[0]["id"]) not in prompt
        assert batch == [
            {
                "ref": "S1",
                "text": sources[0]["text"],
                "locator": sources[0]["locator"],
            }
        ]
        return {
            "concepts": [
                {"label": "Concepto", "description": "Tema", "sourceRefs": ["S1"]}
            ]
        }

    monkeypatch.setattr(concepts, "model_json", fake_model_json)
    concepts.extract_candidates(sources, "test-model", lambda: 30)


def test_extract_candidates_resolves_references_within_each_batch(monkeypatch):
    sources = [source(0), source(1)]
    calls = []

    def fake_model_json(_model, _prompt, batch, _timeout):
        calls.append(batch)
        assert [item["ref"] for item in batch] == ["S1"]
        return {
            "concepts": [
                {
                    "label": f"Concepto {len(calls)}",
                    "description": "Tema",
                    "sourceRefs": ["S1"],
                }
            ]
        }

    monkeypatch.setattr(concepts, "model_json", fake_model_json)
    result = concepts.extract_candidates(sources, "test-model", lambda: 30)

    assert len(calls) == 2
    assert result["concepto 1"]["source_ids"] == {sources[0]["id"]}
    assert result["concepto 2"]["source_ids"] == {sources[1]["id"]}
