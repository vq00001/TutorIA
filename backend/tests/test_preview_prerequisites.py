import json
import sys

import pytest

from backend.experiments import preview_prerequisites


def test_fixture_preserves_the_40_shared_nodes():
    nodes = preview_prerequisites.load_candidates(preview_prerequisites.DEFAULT_NODES)

    assert len(nodes) == 40
    assert next(iter(nodes.values()))["label"] == "Definición de integral doble"
    assert list(nodes.values())[-1]["label"] == "volume under a paraboloid"


@pytest.mark.parametrize(
    "nodes",
    [
        [{"label": "A", "description": ""}],
        [{"label": "A", "description": ""}, {"label": "Á", "description": ""}],
        [{"label": "A"}, {"label": "B", "description": ""}],
    ],
)
def test_rejects_invalid_fixture(tmp_path, nodes):
    file = tmp_path / "nodes.json"
    file.write_text(json.dumps(nodes), encoding="utf-8")

    with pytest.raises(ValueError):
        preview_prerequisites.load_candidates(file)


def test_preview_calls_only_prerequisite_generation(monkeypatch, capsys):
    seen = {}

    def fake_proposals(candidates, model, remaining):
        seen["count"] = len(candidates)
        seen["model"] = model
        assert remaining() == 180
        return [
            {
                "source_key": "definicion de integral doble",
                "target_key": "interpretacion de la integral doble",
                "reason": "Ejemplo",
            }
        ]

    monkeypatch.setattr(preview_prerequisites, "propose_prerequisites", fake_proposals)
    monkeypatch.setenv("MODEL_LLM", "test-model")
    monkeypatch.setattr(sys, "argv", ["preview_prerequisites"])

    preview_prerequisites.main()

    assert seen == {"count": 40, "model": "test-model"}
    output = capsys.readouterr().out
    assert "Nodos cargados: 40" in output
    assert "Definición de integral doble → Interpretación de la integral doble" in output


def test_preview_consolidates_before_proposing_prerequisites(monkeypatch, capsys):
    calls = []

    def fake_consolidation(candidates, target, model, remaining):
        calls.append(("consolidate", len(candidates), target, model, remaining()))
        first_two = list(candidates)[:2]
        merged = {"definicion e interpretacion": {
            "label": "Definición e interpretación", "description": "Concepto fusionado",
            "source_ids": set(),
        }}
        merged.update({key: value for key, value in candidates.items() if key not in first_two})
        return merged, [{"from_keys": first_two, "to_key": "definicion e interpretacion",
                         "reason": "Solapados"}]

    def fake_prerequisites(candidates, model, remaining):
        calls.append(("prerequisites", len(candidates), model, remaining()))
        assert "definicion e interpretacion" in candidates
        return []

    monkeypatch.setattr(preview_prerequisites, "consolidate_candidates", fake_consolidation)
    monkeypatch.setattr(preview_prerequisites, "propose_prerequisites", fake_prerequisites)
    monkeypatch.setenv("MODEL_LLM", "test-model")
    monkeypatch.setattr(sys, "argv", ["preview_prerequisites", "--target-concepts", "20"])

    preview_prerequisites.main()

    assert calls == [
        ("consolidate", 40, 20, "test-model", 180),
        ("prerequisites", 39, "test-model", 180),
    ]
    assert "Consolidación: 40 → 39 nodos" in capsys.readouterr().out
