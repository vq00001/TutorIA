# Contrato de datos de TutorIA

Este contrato describe la frontera entre React y la API que en el futuro consultará la base de datos. `src/services/databaseApi.ts` implementa hoy esos métodos con datos simulados y respuestas serializadas como JSON.

## Formato común

Todas las respuestas usan el sobre `{ "data": recurso }`. Las fechas de la API son ISO 8601 en UTC; el cliente las adapta a `Date` para presentación. Los fallos de servicio deben rechazarse y producir el estado de error de página.

## Operaciones de lectura

| Método | Endpoint sugerido | Contenido de `data` |
| --- | --- | --- |
| `getStudentProfile()` | `GET /api/v1/student/me` | Perfil del usuario |
| `getDashboardSummary()` | `GET /api/v1/dashboard/summary` | Métricas y roadmap |
| `getCourses()` | `GET /api/v1/courses` | Cursos disponibles |
| `getCourseMaterials(courseId)` | `GET /api/v1/courses/{courseId}/materials` | Materiales de ese curso |
| `getCourseMessages(courseId)` | `GET /api/v1/courses/{courseId}/messages` | Mensajes iniciales del chat |
| `getCourseGraph(courseId)` | `GET /api/v1/courses/{courseId}/knowledge-graph` | Exportación Graphology |

### Perfil

`StudentProfile`: `id`, `displayName`, `role`. Nombre y rol se muestran en el saludo, el menú y el avatar.

### Resumen

`DashboardSummary` expone `studyHours`, `studyHoursPeriod`, `studyHoursByDay` (siete números de lunes a domingo), `streakDays`, `masteredConcepts`, `masteredConceptsChange`, `roadmapStep`, `roadmapTotal`, `nextSessionMinutes` y `nextCourseId`. Este último debe referenciar un curso retornado por `getCourses()`.

### Curso

Cada `Course` contiene `id` (clave estable de ruta), `name`, `subject`, `progress` (porcentaje 0–100), `color`, `icon`, `nextTopic` y `lastActivity`. UI usa esos campos para tarjetas, navegación y chat.

### Material

`MaterialDto` contiene `id`, `courseId`, `name`, `size` (bytes) y `addedAt` (fecha ISO). La respuesta de materiales debe pertenecer al `courseId` solicitado. La interfaz permite solo `.txt` por ahora y pasa el id del curso al añadir o quitar registros.

### Mensajes

`ChatMessageDto`: `id`, `role` (`user` o `assistant`), `createdAt`, `parts` y `awaitingResponse` opcional. Partes permitidas: `{ "type": "markdown", "content": "..." }` o `{ "type": "chart", "chart": { "title": "...", "data": [], "series": [] } }`. Markdown puede contener matemáticas y bloques Mermaid; gráficos usan datos estructurados para Recharts.

### Grafo

`GraphologyExport` contiene `nodes` y `edges`, admitidos como objetos o tuplas del formato exportado por Graphology. Atributos de nodo pueden incluir `label`, coordenadas `x`/`y`, `size`, `color` y `kind`; aristas incluyen `source`, `target`, `key` opcional y atributos visuales. Para cursos sin grafo, devolver `{ "data": { "nodes": [], "edges": [] } }`.

## Operaciones de escritura

- `createCourseMaterials(courseId, files)` recibe nombre y tamaño de cada `.txt`, y responde un `MaterialDto[]` creado con fecha ISO. Para producción se debe añadir transporte de bytes, almacenamiento y validación; el mock no guarda los contenidos.
- `deleteCourseMaterial(courseId, materialId)` corresponde a `DELETE /api/v1/courses/{courseId}/materials/{materialId}` y devuelve `{ "data": { "deleted": boolean } }`. El backend debe comprobar que ese material pertenece al curso indicado.

## Carga de datos

`AppDataProvider` obtiene perfil, resumen y cursos; con cada id carga materiales, mensajes y grafo. Expone colecciones como mapas indexados por curso. Inicio, chat y biblioteca leen el mismo estado compartido. Para conectar un backend, implementa `TutorIADataApi` e inyéctalo en el proveedor. Los componentes no deben consultar directamente la base de datos.
