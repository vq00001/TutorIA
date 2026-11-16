"""Adaptador mínimo de Ollama para los experimentos del grafo."""

import json

import requests

from backend.config import Config


def model_json(model, system, data, timeout):
    response = requests.post(
        Config.from_env().ollama_url + "/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)},
            ],
        },
        timeout=(5, timeout),
    )
    response.raise_for_status()
    return json.loads(response.json()["message"]["content"])
