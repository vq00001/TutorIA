from uuid import uuid4

import pytest

from backend.knowledge_graph import consolidation


def candidates():
    return {
        "jacobiano para volumen": {
            "label": "Jacobiano para volumen",
            "description": "Factor de escala",
            "source_ids": {uuid4()},
        },
        "jacobiano en integrales de volumen": {
            "label": "Jacobiano en integrales de volumen",
            "description": "Cambio de variables",
            "source_ids": {uuid4()},
        },
        "coordenadas polares": {
            "label": "Coordenadas polares",
            "description": "Radio y ángulo",
            "source_ids": {uuid4()},
        },
    }


def merge(refs=("C1", "C2"), label="Jacobiano de volumen"):
    return {
        "refs": list(refs),
        "label": label,
        "description": "Factor de cambio de volumen",
        "reason": "Conceptos solapados",
    }


def test_merges_nodes_and_unions_sources_without_mutating_input(monkeypatch):
    nodes = candidates()
    original_sources = set().union(*(node["source_ids"] for node in nodes.values()))

    def fake_model(_model, prompt, data, _timeout):
        assert "objetivo aproximado es 2" in prompt
        assert [item["ref"] for item in data] == ["C1", "C2", "C3"]
        assert all(set(item) == {"ref", "label", "description"} for item in data)
        return {"merges": [merge()]}

    monkeypatch.setattr(consolidation, "model_json", fake_model)
    result, applied = consolidation.consolidate_candidates(nodes, 2, "test", lambda: 30)

    assert len(result) == 2
    assert result["jacobiano de volumen"]["source_ids"] == (
        nodes["jacobiano para volumen"]["source_ids"]
        | nodes["jacobiano en integrales de volumen"]["source_ids"]
    )
    assert set().union(*(node["source_ids"] for node in result.values())) == original_sources
    assert len(nodes) == 3
    assert applied[0]["from_keys"] == [
        "jacobiano para volumen",
        "jacobiano en integrales de volumen",
    ]


def test_does_not_force_merges(monkeypatch):
    monkeypatch.setattr(consolidation, "model_json", lambda *_: {"merges": []})
    result, applied = consolidation.consolidate_candidates(candidates(), 1, "test", lambda: 30)
    assert len(result) == 3
    assert applied == []


def test_skips_single_node_merge_and_keeps_other_valid_merges(monkeypatch):
    nodes = candidates()
    monkeypatch.setattr(
        consolidation,
        "model_json",
        lambda *_: {"merges": [merge(("C3",)), merge()]},
    )

    result, applied = consolidation.consolidate_candidates(nodes, 2, "test", lambda: 30)

    assert len(result) == 2
    assert result["coordenadas polares"] == nodes["coordenadas polares"]
    assert applied == [
        {
            "from_keys": ["jacobiano para volumen", "jacobiano en integrales de volumen"],
            "to_key": "jacobiano de volumen",
            "reason": "Conceptos solapados",
        }
    ]


def test_single_node_merge_alone_leaves_candidates_unchanged(monkeypatch):
    nodes = candidates()
    monkeypatch.setattr(consolidation, "model_json", lambda *_: {"merges": [merge(("C3",))]})

    result, applied = consolidation.consolidate_candidates(nodes, 2, "test", lambda: 30)

    assert result == nodes
    assert applied == []


def test_skips_group_that_would_overshoot_target(monkeypatch):
    monkeypatch.setattr(
        consolidation,
        "model_json",
        lambda *_: {"merges": [merge(("C1", "C2", "C3"))]},
    )
    result, applied = consolidation.consolidate_candidates(candidates(), 2, "test", lambda: 30)
    assert len(result) == 3
    assert applied == []


@pytest.mark.parametrize(
    "merges,error",
    [
        ([merge(("C1", "C4"))], "INVALID_CONSOLIDATION_REF"),
        ([merge(("C1", "C1"))], "INVALID_CONSOLIDATION_REF"),
        ([merge(), merge(("C2", "C3"))], "OVERLAPPING_CONSOLIDATION_GROUPS"),
        ([merge(label="Coordenadas polares")], "CONSOLIDATION_LABEL_COLLISION"),
    ],
)
def test_rejects_invalid_merges(monkeypatch, merges, error):
    monkeypatch.setattr(consolidation, "model_json", lambda *_: {"merges": merges})
    with pytest.raises(ValueError, match=error):
        consolidation.consolidate_candidates(candidates(), 1, "test", lambda: 30)


def test_rejects_invalid_target():
    with pytest.raises(ValueError, match="INVALID_TARGET_CONCEPT_COUNT"):
        consolidation.consolidate_candidates(candidates(), 0, "test", lambda: 30)
