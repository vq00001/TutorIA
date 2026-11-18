"""Pruebas del AI gateway (LiteLLM).

Dos grupos:

1. TestGatewayRouting (unitarias): no necesitan servicios. Verifican que
   `generate_answer` envía el tráfico al gateway y NO directo a Ollama.

2. TestGatewayLive (integración): necesitan el stack levantado
   (`docker compose up -d ollama litellm`). Verifican que el tráfico pasa
   realmente por LiteLLM y que, sin API keys de OpenAI/Gemini, el alias
   `chat-principal` cae al modelo local (Ollama).
   Si el gateway no responde, se omiten automáticamente.

Ejecución (desde la raíz del repo):

    python -m unittest backend.tests.test_gateway -v
    # o, si usas pytest:
    pytest backend/tests/test_gateway.py -v -s
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from backend.core.config import Config
from backend.integrations.llm import generate_answer
from typing import Any

DOCUMENTS = [{"filename": "notas.md", "text": "La capital de Chile es Santiago."}]
QUESTION = "¿Cuál es la capital de Chile?"


def _make_config(**overrides) -> Config:
    params: dict[str, Any] = dict(
        ollama_url="http://ollama.invalid:11434",
        model_embed="all-minilm",
        model_llm="chat-principal",
        gateway_url="http://gateway.test:4000",
        gateway_api_key="sk-test",
    )
    params.update(overrides)
    return Config(**params)


def _fake_response(body: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = body
    return response


class TestGatewayRouting(unittest.TestCase):
    """Pruebas con `requests.post` mockeado: no requieren servicios."""

    def test_posts_to_gateway_endpoint_with_auth_and_alias(self):
        body = {"choices": [{"message": {"content": "Santiago [1]"}}]}
        with patch("requests.post", return_value=_fake_response(body)) as post:
            generate_answer(_make_config(), QUESTION, DOCUMENTS)

        post.assert_called_once()
        args, kwargs = post.call_args
        url = args[0] if args else kwargs["url"]
        self.assertEqual(url, "http://gateway.test:4000/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(kwargs["json"]["model"], "chat-principal")

    def test_does_not_send_ollama_specific_fields(self):
        body = {"choices": [{"message": {"content": "ok"}}]}
        with patch("requests.post", return_value=_fake_response(body)) as post:
            generate_answer(_make_config(), QUESTION, DOCUMENTS)

        payload = post.call_args.kwargs["json"]
        for field in ("think", "options", "num_predict"):
            self.assertNotIn(field, payload)

    def test_never_calls_ollama_directly(self):
        config = _make_config()
        body = {"choices": [{"message": {"content": "ok"}}]}
        with patch("requests.post", return_value=_fake_response(body)) as post:
            generate_answer(config, QUESTION, DOCUMENTS)

        for call in post.call_args_list:
            url = call.args[0] if call.args else call.kwargs["url"]
            self.assertNotIn(config.ollama_url, url)
            self.assertNotIn("/api/chat", url)

    def test_parses_openai_format_and_strips_think_blocks(self):
        body = {"choices": [{"message": {"content": "<think>razonando...</think>Santiago [1]"}}]}
        with patch("requests.post", return_value=_fake_response(body)):
            answer = generate_answer(_make_config(), QUESTION, DOCUMENTS)
        self.assertEqual(answer, "Santiago [1]")

    def test_invalid_response_raises_value_error(self):
        for bad_body in ({}, {"choices": []}, {"message": {"content": "x"}}):
            with patch("requests.post", return_value=_fake_response(bad_body)):
                with self.assertRaises(ValueError):
                    generate_answer(_make_config(), QUESTION, DOCUMENTS)

    def test_no_documents_makes_no_request(self):
        with patch("requests.post") as post:
            answer = generate_answer(_make_config(), QUESTION, [])
        post.assert_not_called()
        self.assertIn("No encontré", answer)


class TestGatewayLive(unittest.TestCase):
    """Pruebas contra el gateway real. Se omiten si no está disponible."""

    @classmethod
    def setUpClass(cls):
        cls.config = Config.from_env()
        cls.base = cls.config.gateway_url
        cls.auth = {"Authorization": f"Bearer {cls.config.gateway_api_key}"}
        try:
            requests.get(f"{cls.base}/health/liveliness", timeout=5).raise_for_status()
        except requests.RequestException as exc:
            raise unittest.SkipTest(
                f"AI gateway no disponible en {cls.base} ({exc}). "
                "Levántalo con: docker compose up -d ollama litellm"
            )

    def _chat(self, model: str, headers: dict | None = None) -> requests.Response:
        return requests.post(
            f"{self.base}/v1/chat/completions",
            headers=self.auth if headers is None else headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Responde solo: ok"}],
                "max_tokens": 300,
                "temperature": 0,
            },
            timeout=180,
        )

    def test_gateway_requires_authentication(self):
        """Sin Bearer token, LiteLLM rechaza: prueba que hablamos con el gateway."""
        response = self._chat("chat-principal", headers={})
        self.assertIn(response.status_code, (401, 403), response.text)

    def test_gateway_exposes_expected_aliases(self):
        response = requests.get(f"{self.base}/v1/models", headers=self.auth, timeout=10)
        self.assertEqual(response.status_code, 200, response.text)
        ids = {item["id"] for item in response.json()["data"]}
        for alias in ("chat-principal", "chat-premium", "local-llama", "embeddings"):
            self.assertIn(alias, ids)

    def test_request_is_handled_by_litellm(self):
        """Las respuestas de LiteLLM incluyen cabeceras x-litellm-*."""
        response = self._chat("local-llama")
        self.assertEqual(response.status_code, 200, response.text)
        litellm_headers = [h for h in response.headers if h.lower().startswith("x-litellm")]
        self.assertTrue(
            litellm_headers,
            f"Sin cabeceras x-litellm-*: el tráfico no pasó por LiteLLM. "
            f"Cabeceras: {dict(response.headers)}",
        )

    def test_fallback_to_local_model_when_no_provider_keys(self):
        """Sin OPENAI_API_KEY ni GEMINI_API_KEY, chat-principal debe usar Ollama."""
        if os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY"):
            self.skipTest(
                "OPENAI_API_KEY o GEMINI_API_KEY están definidas en el entorno/.env; "
                "vacíalas y reinicia litellm para probar el fallback."
            )

        response = self._chat("chat-principal")
        self.assertEqual(response.status_code, 200, response.text)

        body = response.json()
        served_model = str(body.get("model", "")).lower()
        api_base = response.headers.get("x-litellm-model-api-base", "").lower()
        fallbacks = response.headers.get("x-litellm-attempted-fallbacks", "0")

        print(
            f"\n[fallback] model={served_model!r} api_base={api_base!r} "
            f"attempted_fallbacks={fallbacks!r}"
        )

        self.assertNotIn("gpt", served_model, "Respondió OpenAI, no el modelo local")
        self.assertNotIn("gemini", served_model, "Respondió Gemini, no el modelo local")

        used_local = (
            "qwen" in served_model
            or "ollama" in api_base
            or (fallbacks.isdigit() and int(fallbacks) >= 1)
        )
        self.assertTrue(
            used_local,
            f"No hay evidencia de que se usó el modelo local. body.model={served_model!r}, "
            f"api_base={api_base!r}, attempted_fallbacks={fallbacks!r}",
        )

    def test_generate_answer_end_to_end_through_gateway(self):
        """El flujo real de la app (generate_answer) obtiene respuesta vía gateway."""
        answer = generate_answer(self.config, QUESTION, DOCUMENTS)
        self.assertIsInstance(answer, str)
        self.assertTrue(answer.strip())
        self.assertNotIn("<think>", answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
