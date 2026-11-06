"""Deterministic provider double: known vectors and schema-specific generations."""

import json

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "provider": "synthetic-stub"}


@app.get("/api/tags")
def tags():
    return {"models": [{"name": "synthetic", "digest": "fixture-v1"}]}


@app.post("/api/embed")
def embed(body: dict):
    values = body["input"]
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values:
        # Deliberately discriminate unrelated queries, rather than giving every
        # source/query the same vector and thereby hiding ranking defects.
        low = value.lower()
        result.append(
            [0, 1, 0]
            if any(s in low for s in ("unrelated", "sin evidencia", "astronom"))
            else [1, 0, 0]
        )
    return {"model": "synthetic", "embeddings": result}


@app.post("/api/chat")
def chat(body: dict):
    system = body["messages"][0]["content"]
    user = json.loads(body["messages"][-1]["content"])
    if "concepts" in system:
        sources = user
        result = {
            "concepts": [
                {
                    "label": "Derivada",
                    "description": "La derivada mide la tasa de cambio.",
                    "sourceIds": [sources[0]["id"]],
                }
            ]
        }
    elif "questions" in system:
        sources = user["sources"]
        result = {
            "questions": [
                dict(
                    conceptId=sources[0]["conceptId"],
                    sourceIds=[sources[0]["sourceId"]],
                    kind="alternatives",
                    prompt=f"Pregunta sintética {i + 1}: ¿qué expresa la derivada?",
                    options=["Tasa de cambio", "Una constante universal"],
                    correct=[0],
                    expectedAnswer=None,
                    rubric=None,
                    explanation="La fuente describe la tasa de cambio.",
                )
                for i in range(user["count"])
            ]
        }
    elif "points" in system:
        result = {
            "points": [c["maxPoints"] for c in user["rubric"]["criteria"]],
            "feedback": "Evaluación sintética de rúbrica.",
        }
    else:
        result = {
            "answer": "La derivada mide la tasa de cambio según el material.",
            "citations": [user["sources"][0]["id"]],
        }
    return {
        "model": "synthetic",
        "message": {"role": "assistant", "content": json.dumps(result)},
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 10,
    }
