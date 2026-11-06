"""Probe the actual installed Ollama model and register its immutable space."""

import os
from uuid import uuid4

import requests
from sqlalchemy import create_engine, text


def main():
    url = os.environ["OLLAMA_URL"].rstrip("/")
    name = os.environ["MODEL_EMBED"]
    response = requests.get(url + "/api/tags", timeout=(5, 30))
    response.raise_for_status()
    models = response.json()["models"]
    model = next((m for m in models if m["name"] in (name, name + ":latest")), None)
    if not model:
        raise RuntimeError("Embedding model is not installed; pull the explicit model first")
    response = requests.post(
        url + "/api/embed",
        json={"model": name, "input": ["TutorIA dimension probe"]},
        timeout=(5, 60),
    )
    response.raise_for_status()
    dimension = len(response.json()["embeddings"][0])
    if not 1 <= dimension <= 2000:
        raise RuntimeError("Unsupported embedding dimension")
    with create_engine(os.environ["MIGRATION_DATABASE_URL"]).begin() as conn:
        identifier = conn.execute(
            text("""INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension)
          VALUES(:id,'ollama',:name,:revision,:dim)
          ON CONFLICT(provider,model_name,model_revision,dimension,distance,normalization,query_prefix,document_prefix)
          DO UPDATE SET enabled=true RETURNING id"""),
            dict(id=uuid4(), name=name, revision=model["digest"], dim=dimension),
        ).scalar_one()
    print("EMBEDDING_SPACE_ID=" + str(identifier))
    llm = os.environ.get("MODEL_LLM")
    for m in models:
        if m["name"] in (llm, str(llm) + ":latest"):
            print("MODEL_LLM_REVISION=" + m["digest"])


if __name__ == "__main__":
    main()
