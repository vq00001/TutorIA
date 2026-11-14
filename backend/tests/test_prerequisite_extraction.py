from uuid import uuid4

import pytest

from backend.experiments.knowledge_graph import prerequisites


def candidates():
    return {
        "coordenadas polares": {
            "label": "Coordenadas polares",
            "description": "Representación mediante radio y ángulo",
            "source_ids": {uuid4()},
        },
        "integral doble": {
            "label": "Integral doble",
            "description": "Integración sobre una región del plano",
            "source_ids": {uuid4()},
        },
        "integral doble en polares": {
            "label": "Integral doble en polares",
            "description": "Integración doble usando coordenadas polares",
            "source_ids": {uuid4()},
        },
    }


def proposal(source, target):
    return {"sourceRef": source, "targetRef": target, "reason": "Se necesita antes"}


def test_proposals_use_only_existing_nodes_and_preserve_direction(monkeypatch):
    nodes = candidates()

    def fake_model_json(_model, _prompt, data, _timeout):
        assert [item["ref"] for item in data] == ["C1", "C2", "C3"]
        assert all(set(item) == {"ref", "label", "description"} for item in data)
        assert [item["label"] for item in data] == [node["label"] for node in nodes.values()]
        return {"prerequisites": [proposal("C1", "C3"), proposal("C2", "C3")]}

    monkeypatch.setattr(prerequisites, "model_json", fake_model_json)
    result = prerequisites.propose_prerequisites(nodes, "test-model", lambda: 30)

    assert result == [
        {
            "source_key": "coordenadas polares",
            "target_key": "integral doble en polares",
            "reason": "Se necesita antes",
        },
        {
            "source_key": "integral doble",
            "target_key": "integral doble en polares",
            "reason": "Se necesita antes",
        },
    ]


@pytest.mark.parametrize("source,target", [("C4", "C1"), ("C1", "C4")])
def test_rejects_unknown_node(monkeypatch, source, target):
    monkeypatch.setattr(
        prerequisites,
        "model_json",
        lambda *_: {"prerequisites": [proposal(source, target)]},
    )
    with pytest.raises(ValueError, match="INVALID_PREREQUISITE_NODE"):
        prerequisites.propose_prerequisites(candidates(), "test-model", lambda: 30)


def test_rejects_self_dependency(monkeypatch):
    monkeypatch.setattr(
        prerequisites,
        "model_json",
        lambda *_: {"prerequisites": [proposal("C1", "C1")]},
    )
    with pytest.raises(ValueError, match="SELF_PREREQUISITE"):
        prerequisites.propose_prerequisites(candidates(), "test-model", lambda: 30)


def test_skips_duplicate_pair_and_keeps_first_reason(monkeypatch):
    duplicate = proposal("C1", "C2") | {"reason": "Motivo repetido"}
    monkeypatch.setattr(
        prerequisites,
        "model_json",
        lambda *_: {"prerequisites": [proposal("C1", "C2"), duplicate, proposal("C3", "C2")]},
    )
    result = prerequisites.propose_prerequisites(candidates(), "test-model", lambda: 30)

    assert result == [
        {
            "source_key": "coordenadas polares",
            "target_key": "integral doble",
            "reason": "Se necesita antes",
        },
        {
            "source_key": "integral doble en polares",
            "target_key": "integral doble",
            "reason": "Se necesita antes",
        },
    ]


def test_rejects_cycle(monkeypatch):
    monkeypatch.setattr(
        prerequisites,
        "model_json",
        lambda *_: {
            "prerequisites": [
                proposal("C1", "C2"),
                proposal("C2", "C3"),
                proposal("C3", "C1"),
            ]
        },
    )
    with pytest.raises(ValueError, match="PREREQUISITE_CYCLE"):
        prerequisites.propose_prerequisites(candidates(), "test-model", lambda: 30)
