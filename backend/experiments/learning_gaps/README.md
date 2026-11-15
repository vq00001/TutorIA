# Experimento de falencias previas

Usa los 40 nodos ya extraídos en `../fixtures/integracion_multivariable_nodes.json`.
`scores.json` contiene dos resultados simulados por concepto. El inferidor común
está en `backend/knowledge_graph/learning_gaps.py` y también lo usa la ruta HTTP.
Este experimento no usa la base de datos, `learning.py`, el roadmap ni modifica
el grafo del curso.

La ruta `POST /api/v1/courses/{courseId}/knowledge-graph/learning-gaps/preview`
acepta nodos y resultados explícitos, sin leer evaluaciones ni guardar cambios.
Pueden usarla los miembros de una clase activa. `C1`, `C2`, etc. identifican
los nodos por su posición en la lista; la respuesta usa esas referencias y
marca las sugerencias como hipótesis. Ejemplo de cuerpo:

```json
{
  "nodes": [
    {"label": "Integral doble", "description": "Integrar sobre una región"},
    {"label": "Jacobiano", "description": "Cambio de variables"}
  ],
  "results": [{"ref": "C2", "correct": 2, "total": 10}],
  "threshold": 0.4
}
```

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
