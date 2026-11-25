"""Contrato de completitud de las respuestas del gateway, con HTTP simulado."""

from types import SimpleNamespace

import pytest

from backend.core.config import Config
from backend.integrations import llm


@pytest.fixture
def gateway(monkeypatch):
    config = Config(
        ollama_url="http://ollama.invalid",
        model_embed="unused",
        model_llm="chat-test",
        gateway_url="http://gateway.invalid",
    )
    body = {"choices": []}

    def post(url, **kwargs):
        assert url == "http://gateway.invalid/v1/chat/completions"
        assert kwargs["json"]["stream"] is False
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    def unexpected_network(*args, **kwargs):
        pytest.fail("El test intentó hacer una petición de red sin simular")

    monkeypatch.setattr(llm.requests.sessions.Session, "request", unexpected_network)
    monkeypatch.setattr(llm.requests, "post", post)
    return config, body


def completion(body, content, reason):
    body["choices"] = [{"message": {"content": content}, "finish_reason": reason}]


@pytest.mark.parametrize(
    "content",
    ["La explicación termina aquí.", "# Desarrollo\n\n" + "Paso completo. " * 1000 + "\nFIN"],
    ids=["breve", "larga"],
)
def test_complete_response_preserves_all_content(gateway, content):
    config, body = gateway
    completion(body, content, "stop")

    assert llm.chat_completion(config, "Pregunta sintética") == content


@pytest.mark.parametrize(
    "content",
    ["La demostración continúa con", "Primera parte terminada."],
    ids=["frase_incompleta", "frase_con_punto"],
)
def test_length_limited_response_is_not_accepted_as_complete(gateway, content):
    config, body = gateway
    completion(body, content, "length")

    # La interfaz actual devuelve str y usa ValueError para respuestas inválidas.
    # Exige señalar el corte incluso cuando el texto termina con un punto.
    with pytest.raises(ValueError):
        llm.chat_completion(config, "Pregunta sintética")


def test_response_without_finish_reason_keeps_existing_compatibility(gateway):
    config, body = gateway
    body["choices"] = [{"message": {"content": "Respuesta del proveedor."}}]

    assert llm.chat_completion(config, "Pregunta sintética") == "Respuesta del proveedor."


def test_empty_response_is_rejected(gateway):
    config, body = gateway
    completion(body, "   ", "stop")

    with pytest.raises(ValueError):
        llm.chat_completion(config, "Pregunta sintética")
