# Generación experimental del grafo

Esta carpeta conserva los algoritmos de extracción de conceptos, consolidación
y propuesta de prerrequisitos de `feature/consolidacion-grafo`. No persiste
resultados en PostgreSQL ni modifica el catálogo de `only_database`.

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
