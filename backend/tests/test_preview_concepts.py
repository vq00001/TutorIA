import sys
from types import SimpleNamespace

from backend.experiments import preview_concepts


def test_cli_passes_file_text_to_graph_generator(tmp_path, monkeypatch, capsys):
    file = tmp_path / "apunte.md"
    file.write_text("Integral doble sobre una región.", encoding="utf-8")
    received = {}

    def fake_generate(sources, config, *, target_concepts, include_prerequisites):
        received["source"] = sources[0]
        received["model"] = config.model_llm
        received["target"] = target_concepts
        received["prerequisites"] = include_prerequisites
        return SimpleNamespace(
            inputs=[SimpleNamespace(fragment_count=1)],
            nodes=[SimpleNamespace(
                ref="N1", label="Integral doble", description="Tema del apunte",
                sources=[SimpleNamespace(
                    title="apunte.md", section_path=["Integrales"], chunk_index=0,
                    source_id="archivo-1",
                )],
            )],
            merges=[], edges=[],
        )

    monkeypatch.setattr(preview_concepts, "generate_graph_preview", fake_generate)
    monkeypatch.setenv("MODEL_LLM", "test-model")
    monkeypatch.setattr(sys, "argv", [
        "preview_concepts", str(file), "--target-concepts", "1", "--with-prerequisites",
    ])

    preview_concepts.main()

    assert received["source"].text == file.read_text(encoding="utf-8")
    assert received["source"].title == file.name
    assert received["model"] == "test-model"
    assert received["target"] == 1
    assert received["prerequisites"] is True
    output = capsys.readouterr().out
    assert "Fragmentos enviados: 1" in output
    assert "Fuente: apunte.md, Integrales, fragmento 1 (archivo-1)" in output
    assert "Dependencias propuestas: 0" in output
