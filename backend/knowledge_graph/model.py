"""Adaptador JSON del grafo mediante el AI gateway."""

import json

from backend.core.config import Config
from backend.integrations.llm import chat_completion


def model_json(model, system, data, timeout):
    config = Config.from_env()
    content = chat_completion(
        config,
        f"{system}\n\nDatos:\n{json.dumps(data, ensure_ascii=False, default=str)}",
        model=model,
        max_tokens=2000,
        timeout=timeout,
        response_format={"type": "json_object"},
    )
    return json.loads(content)
