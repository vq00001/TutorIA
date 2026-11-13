# Experimento de falencias previas

Usa los 40 nodos ya extraídos en `../fixtures/integracion_multivariable_nodes.json`.
`scores.json` contiene dos resultados simulados por concepto. No usa la base de
datos, `learning.py`, el roadmap ni modifica el grafo del curso.

Con las dependencias del backend instaladas y Ollama levantado, desde la raíz del proyecto:

```bash
uv run --project backend python -m backend.experiments.learning_gaps.preview
```

Se pueden pasar otros archivos con `--nodes` y `--scores`, y cambiar el umbral
con `--threshold` (por defecto 0.4). Un resultado bajo inicia una **hipótesis**:
no confirma que el estudiante desconozca el prerrequisito sugerido. Esta prueba
recomienda repasar conceptos existentes o propone temas nuevos. No crea
relaciones ni incorpora relaciones generadas previamente: el conjunto fijo solo
guarda nodos.
