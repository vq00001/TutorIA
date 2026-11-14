# Generación experimental del grafo

Los algoritmos de extracción, consolidación y propuesta de prerrequisitos
están ahora en [backend/knowledge_graph/](../../knowledge_graph/). Los
experimentos no persisten resultados en PostgreSQL ni modifican el catálogo.

Desde la raíz del repositorio, con las dependencias del backend instaladas y
Ollama disponible en `OLLAMA_URL`:

```bash
uv run --project backend python -m backend.experiments.preview_concepts recursos/integracion_multivariable_calculo_iii.md --target-concepts 20 --with-prerequisites
uv run --project backend python -m backend.experiments.preview_prerequisites
uv run --project backend python -m backend.experiments.learning_gaps.preview
```

Las previsualizaciones de conceptos aceptan `.md`, `.markdown` y `.txt`. Los
identificadores de fuente son los fragmentos parent del chunker actual de
`only_database`; se muestran sección e índice del fragmento. La integración
posterior con el catálogo y los trabajos de base de datos requiere una
adaptación específica al backend actual.
