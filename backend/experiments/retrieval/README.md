# Experimentos de recuperación

Esta rama contiene el benchmark para comparar modelos de embeddings, elegir
`score_threshold` y estudiar `limit`.

- `data/documents/`: chunks indexables (`documents.json` y `documents_many.json`).
- `data/cases/`: consultas y documentos esperados (`test_cases*.json`).
- `results/`: reportes generados, no usados como datasets.
- `output.py`: resuelve reportes dentro de `results/`.
- `repository.py`: adaptador de embeddings y Qdrant para el benchmark.
- `run.py`: evalúa thresholds, precision, recall, MRR y rechazo OOD.
- `evaluate_limit.py`: compara distintos límites de resultados.

Todos los comandos se ejecutan desde la raíz del proyecto:

```bash
backend/.venv/bin/python -m backend.experiments.retrieval.run \
  --reset --model all-minilm
```

```bash
backend/.venv/bin/python -m backend.experiments.retrieval.evaluate_limit \
  --reset --threshold 0.50 --limits 1 2 3 4 5 6 8 10 \
  --output
```

Los resultados son experimentales: revisa recall, precision, MRR y rechazo
fuera de dominio antes de copiar un valor a `.env`.

`evaluate_limit.py` muestra una sugerencia de `LIMIT_QUERY` y la incluye en el
reporte. El criterio maximiza `selection_score`; si dos límites empatan,
recomienda el menor para reducir contexto y costo del LLM.

`--output` sin nombre guarda el JSON con el nombre predeterminado en `results/`.
También puedes usar `--output mi-reporte.json`; los nombres relativos se guardan
automáticamente en esa misma carpeta.
