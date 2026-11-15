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

Un alumno puede aceptar una selección mediante
`POST /api/v1/courses/{courseId}/knowledge-graph/learning-gaps/accept`.
La operación guarda temas y recomendaciones solo para su cuenta. Por ejemplo:

```json
{
  "newNodes": [{"ref": "N1", "label": "Determinantes", "description": "Repaso de álgebra lineal"}],
  "recommendations": [
    {"targetConceptId": "UUID-del-concepto", "newNodeRef": "N1", "reason": "Base previa posible"}
  ]
}
```

`GET /api/v1/courses/{courseId}/knowledge-graph/learning-gaps/accepted` muestra
los refuerzos guardados del alumno autenticado. El grafo común del curso no se
modifica. Los temas nuevos se enlazan con conceptos del curso; recomendar repasar
un concepto ya existente se guarda como recomendación, no como prerrequisito
aprobado.

`GET /api/v1/courses/{courseId}/knowledge-graph/personalized` devuelve al alumno
el grafo del curso junto con sus nodos personales. Cada nodo indica su `origin`:
`course` o `personal`. `edges` contiene únicamente prerrequisitos aprobados del
curso. `recommendations` vincula temas existentes o personales con conceptos del
curso mediante `source` y `target`, con su motivo y tipo; siguen siendo
recomendaciones de repaso, no aristas aprobadas. La ruta no genera contenido con
el modelo ni escribe datos.

Antes de usar las rutas en una base existente, vuelve a ejecutar `db-init` para
crear las tablas y políticas privadas.

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
