import json

import pytest

from backend.experiments.learning_gaps import infer, preview


def nodes():
    return [
        {"label": "Integral doble", "description": "Integrar sobre una región"},
        {"label": "Jacobiano polar", "description": "Factor de área polar"},
        {"label": "Cambio de variables", "description": "Transformar una integral"},
    ]


def test_mock_scores_use_the_existing_40_nodes():
    graph, scores = infer.load_inputs(preview.DEFAULT_NODES, preview.DEFAULT_SCORES)
    assert len(graph) == 40
    assert len(scores) == 2
    assert {graph[int(ref[1:]) - 1]["label"] for ref in scores} == {
        "Jacobiano en coordenadas polares", "Cálculo de integrales iteradas"
    }


def test_low_score_can_recommend_existing_and_new_topics(monkeypatch):
    def fake_model(prompt, data):
        assert "No incluyas targetRef" in prompt
        assert len(data["nodes"]) == 3
        assert data["weakResult"] == {"ref": "C2", "label": "Jacobiano polar",
                                      "description": "Factor de área polar", "correct": 2, "total": 10}
        return {"suggestions": [
            {"existingRef": "C3", "reason": "Repasar cambios"},
            {"newNode": {"label": "Determinantes", "description": "Matrices"},
             "reason": "Posible base faltante"},
        ]}

    monkeypatch.setattr(infer, "model_json", fake_model)
    graph = nodes()
    result = infer.infer_gaps(graph, {"C2": {"correct": 2, "total": 10}})
    assert graph == nodes()
    assert result["new_nodes"] == [
        {"ref": "N1", "label": "Determinantes", "description": "Matrices", "status": "hypothesis"}
    ]
    assert [(r["kind"], r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [
        ("existing", "C3", "C2"), ("new", "N1", "C2")
    ]
    assert "edges" not in result


def test_sufficient_score_does_not_call_model(monkeypatch):
    monkeypatch.setattr(infer, "model_json", lambda *_: pytest.fail("model should not be called"))
    assert infer.infer_gaps(nodes(), {"C2": {"correct": 8, "total": 10}})["recommendations"] == []


@pytest.mark.parametrize("invalid_response", [None, {}, {"suggestions": {}}, {"suggestions": None}])
def test_invalid_model_response_is_empty_and_next_concept_is_processed(monkeypatch, invalid_response):
    def fake_model(_, data):
        if data["weakResult"]["ref"] == "C1":
            return invalid_response
        return {"suggestions": [{"existingRef": "C3", "reason": "Repasar cambios"}]}

    monkeypatch.setattr(infer, "model_json", fake_model)
    scores = {ref: {"correct": 1, "total": 10} for ref in ("C1", "C2")}
    result = infer.infer_gaps(nodes(), scores)
    assert result["discarded"] == 1
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [("C3", "C2")]


def test_discards_malformed_suggestions_and_keeps_valid_ones(monkeypatch):
    monkeypatch.setattr(infer, "model_json", lambda *_: {"suggestions": [
        {"existingRef": "C2", "newNode": {"label": "tema previo", "description": "descripción"},
         "reason": "dos orígenes"},
        {"existingRef": "C9", "reason": "origen desconocido"},
        {"existingRef": "C2", "reason": "mismo nodo"},
        {"existingRef": "C1", "reason": "propuesta válida"},
    ]})
    result = infer.infer_gaps(nodes(), {"C2": {"correct": 2, "total": 10}})
    assert result["discarded"] == 3
    assert result["new_nodes"] == []
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [("C1", "C2")]


def test_new_node_matching_existing_concept_reuses_its_ref(monkeypatch):
    monkeypatch.setattr(infer, "model_json", lambda *_: {"suggestions": [
        {"newNode": {"label": " integral DOBLE ", "description": "x"},
         "reason": "Repasar integrales"},
    ]})
    result = infer.infer_gaps(nodes(), {"C2": {"correct": 2, "total": 10}})
    assert result["new_nodes"] == []
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [("C1", "C2")]
    assert result["recommendations"][0]["kind"] == "existing"


def test_new_node_matching_target_is_ignored(monkeypatch):
    monkeypatch.setattr(infer, "model_json", lambda *_: {"suggestions": [
        {"newNode": {"label": "Jacobiano polar", "description": "x"},
         "reason": "Repetición"},
        {"newNode": {"label": "Determinantes", "description": "Matrices"},
         "reason": "Posible base faltante"},
    ]})
    result = infer.infer_gaps(nodes(), {"C2": {"correct": 2, "total": 10}})
    assert [node["label"] for node in result["new_nodes"]] == ["Determinantes"]
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [("N1", "C2")]


def test_recommendations_for_two_weak_concepts_do_not_create_graph_edges(monkeypatch):
    calls = []
    def fake_model(_, data):
        calls.append(data["weakResult"]["ref"])
        source = "C2" if calls[-1] == "C1" else "C1"
        return {"suggestions": [{"existingRef": source, "reason": "x"}]}

    monkeypatch.setattr(infer, "model_json", fake_model)
    scores = {ref: {"correct": 1, "total": 10} for ref in ("C1", "C2")}
    result = infer.infer_gaps(nodes(), scores)
    assert calls == ["C1", "C2"]
    assert result["discarded"] == 0
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [
        ("C2", "C1"), ("C1", "C2")
    ]
    assert "edges" not in result


def test_rejects_unknown_score_node(tmp_path):
    nodes_file = tmp_path / "nodes.json"
    scores_file = tmp_path / "scores.json"
    nodes_file.write_text(json.dumps(nodes()), encoding="utf-8")
    scores_file.write_text(json.dumps({"results": [{"label": "No existe", "correct": 1, "total": 10}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="UNKNOWN_OR_DUPLICATE_SCORE_NODE"):
        infer.load_inputs(nodes_file, scores_file)


def test_model_cannot_change_deterministic_target(monkeypatch):
    monkeypatch.setattr(infer, "model_json", lambda *_: {"suggestions": [
        {"targetRef": "C1", "existingRef": "C3", "reason": "x"}
    ]})
    result = infer.infer_gaps(nodes(), {"C2": {"correct": 2, "total": 10}})
    assert result["discarded"] == 0
    assert [(r["node_ref"], r["target_ref"]) for r in result["recommendations"]] == [("C3", "C2")]
