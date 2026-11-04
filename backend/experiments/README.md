# Experimentos

Desde la raíz del proyecto, todos los comandos pueden ejecutarse con `make`:

```bash
make calibrate
make evaluate
make evaluate-limit
```

La configuración de dependencias está centralizada en `pyproject.toml`; no es
necesario activar manualmente `backend/.venv`. Ejecuta `make install` una vez
para crear el entorno con `uv`. Para una instalación sin herramientas de
desarrollo usa `make prod`.

Todos los experimentos reproducibles viven en este directorio. Cada rama
contiene su código, datasets y documentación:

```text
experiments/
├── token_estimation/   # calibra TOKEN_ESTIMATE_RATIO
└── retrieval/          # evalúa threshold, limit y modelos de embeddings
```

Los experimentos no modifican producción automáticamente. Generan métricas y
recomendaciones que se pueden copiar a `.env`.

## Estimación de tokens

```bash
backend/.venv/bin/python -m backend.experiments.token_estimation.calibrate \
  --output
```

Usa `prompt_eval_count` del modelo. Prueba `/api/embed` primero y utiliza
`/api/generate` como fallback. El resultado incluye `mean`, `median`, `p95`,
`max` y `recommended_ratio`.

Por defecto usa `token_estimation/data/samples.md`. Para calibrar con
contenido propio, pasa una carpeta o archivo `.md`, `.markdown` o `.txt`.
`--output` sin nombre guarda el resultado como
`token_estimation/results/calibration.json`; también puedes usar
`--output mi-calibracion.json`.

## Recuperación

Evaluación completa de thresholds y rechazo fuera de dominio:

```bash
backend/.venv/bin/python -m backend.experiments.retrieval.run \
  --reset --model all-minilm
```

Comparación de `limit` manteniendo fijo el threshold:

```bash
backend/.venv/bin/python -m backend.experiments.retrieval.evaluate_limit \
  --reset --threshold 0.50 \
  --limits 1 2 3 4 5 6 8 10 \
  --output limit.json
```

`--output` sin nombre usa el nombre predeterminado del experimento. Si se
indica un nombre, también se guarda automáticamente dentro de `results/`.

`evaluate_limit` muestra y guarda una sugerencia de `LIMIT_QUERY`: elige el
mayor `selection_score` y, en empate, el menor límite.
