import pytest

from backend.core.config import Config
from backend.knowledge_graph import concepts, consolidation, prerequisites
from backend.knowledge_graph.preview import TextSource, generate_graph_preview
from backend.rag.chunking import chunk_text


def config(**changes):
    values = {
        "ollama_url": "http://ollama:11434",
        "model_embed": "unused",
        "model_llm": "test-model",
        "min_section_tokens": 1,
        "child_target_tokens": 10,
        "parent_max_tokens": 50,
        "token_estimate_ratio": 1,
    }
    values.update(changes)
    return Config(**values)


def test_every_fragment_of_every_text_is_sent_and_keeps_its_source(monkeypatch):
    settings = config()
    def text_with_paragraphs(prefix):
        return "\n\n".join(
            " ".join(f"{prefix}{index}" for index in range(start, start + 30))
            for start in range(0, 120, 30)
        )

    sources = [
        TextSource(sourceId="apunte-a", title="Apunte A", text=text_with_paragraphs("a")),
        TextSource(sourceId="apunte-b", title="Apunte B", text=text_with_paragraphs("b")),
    ]
    expected = sum(len(chunk_text(item.text, item.title, settings)[0]) for item in sources)
    assert expected > len(sources)
    received = []

    def fake_model_json(_model, _prompt, batch, _remaining):
        received.extend(batch)
        return {"concepts": [
            {"label": "Tema compartido", "description": "Presente en ambos textos",
             "sourceRefs": [item["ref"] for item in batch]}
        ]}

    monkeypatch.setattr(concepts, "model_json", fake_model_json)
    monkeypatch.setattr(concepts, "MAX_BATCH_TOKENS", 35)
    result = generate_graph_preview(sources, settings)

    assert len(received) == expected
    assert [item.fragment_count for item in result.inputs] == [
        len(chunk_text(source.text, source.title, settings)[0]) for source in sources
    ]
    assert len(result.nodes) == 1
    assert [(item.source_id, item.chunk_index) for item in result.nodes[0].sources] == [
        (source.source_id, index)
        for source, count in zip(sources, (item.fragment_count for item in result.inputs))
        for index in range(count)
    ]
    assert result.edges == []
    assert result.stages.consolidation == "skipped"
    assert result.stages.prerequisites == "skipped"


def test_merges_sources_across_texts_and_links_final_node_refs(monkeypatch):
    sources = [
        TextSource(sourceId="uno", title="Uno", text="Integración doble sobre una región."),
        TextSource(sourceId="dos", title="Dos", text="Integral doble sobre un plano."),
        TextSource(sourceId="tres", title="Tres", text="Jacobiano de cambio de variables."),
    ]

    def fake_concepts(_model, _prompt, batch, _remaining):
        return {"concepts": [
            {"label": "Integración doble", "description": "Primera formulación", "sourceRefs": ["S1"]},
            {"label": "Integral doble", "description": "Segunda formulación", "sourceRefs": ["S2"]},
            {"label": "Jacobiano", "description": "Cambio de variables", "sourceRefs": ["S3"]},
        ]}

    monkeypatch.setattr(concepts, "model_json", fake_concepts)
    monkeypatch.setattr(consolidation, "model_json", lambda *_: {"merges": [{
        "refs": ["C1", "C2"], "label": "Integral doble", "description": "Integral en dos variables",
        "reason": "Son el mismo concepto",
    }]})
    monkeypatch.setattr(prerequisites, "model_json", lambda *_: {"prerequisites": [{
        "sourceRef": "C1", "targetRef": "C2", "reason": "Cambio de variables",
    }]})

    result = generate_graph_preview(
        sources, config(), target_concepts=2, include_prerequisites=True
    )

    assert [(node.ref, node.label) for node in result.nodes] == [
        ("N1", "Jacobiano"), ("N2", "Integral doble")
    ]
    assert [source.source_id for source in result.nodes[1].sources] == ["uno", "dos"]
    assert result.merges[0].from_labels == ["Integración doble", "Integral doble"]
    assert result.merges[0].to_ref == "N2"
    assert [(edge.source_ref, edge.target_ref, edge.reason) for edge in result.edges] == [
        ("N1", "N2", "Cambio de variables")
    ]
    assert result.model_dump(by_alias=True)["edges"][0]["sourceRef"] == "N1"
    assert result.stages.consolidation == "completed"
    assert result.stages.prerequisites == "completed"


def test_rejects_empty_or_duplicate_text_sources():
    with pytest.raises(ValueError, match="EMPTY_GRAPH_SOURCES"):
        generate_graph_preview([], config())
    with pytest.raises(ValueError, match="El campo no puede estar vacío"):
        TextSource(sourceId="uno", title="Uno", text="   ")
    source = TextSource(sourceId="uno", title="Uno", text="Texto de prueba.")
    with pytest.raises(ValueError, match="DUPLICATE_GRAPH_SOURCE_ID"):
        generate_graph_preview([source, source], config())
    with pytest.raises(ValueError, match="INVALID_TARGET_CONCEPT_COUNT"):
        generate_graph_preview([source], config(), target_concepts=0)
