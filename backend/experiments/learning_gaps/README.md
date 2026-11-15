# Experimento de falencias previas

Usa los 40 nodos ya extraídos en `../fixtures/integracion_multivariable_nodes.json`.
`scores.json` contiene dos resultados simulados por concepto. El inferidor común
está en `backend/knowledge_graph/learning_gaps.py` y también lo usa la ruta HTTP.
Este experimento no usa la base de datos, `learning.py`, el roadmap ni modifica
el grafo del curso.

La ruta `POST /api/v1/courses/{courseId}/knowledge-graph/learning-gaps/preview`
lee los nodos guardados de la clase y acepta resultados simulados asociados a
sus UUID. Pueden usarla los miembros de una clase activa. Identifica al usuario
mediante su sesión, pero no lee evaluaciones reales ni guarda cambios. La
respuesta usa UUID para conceptos existentes y referencias `N1`, `N2`, etc.
para temas nuevos propuestos; todos siguen siendo hipótesis. Ejemplo de cuerpo:

```json
{
  "results": [{"conceptId": "UUID-de-un-concepto-del-curso", "correct": 2, "total": 10}],
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
