# TutorIA · interfaz base

Aplicación web React + TypeScript para un tutor de aprendizaje. Incluye inicio,
chat de curso, biblioteca de materiales y un grafo de conceptos. Los servicios
se conectan al backend mediante HTTP y conservan adaptadores locales para datos
que todavía no tienen una integración completa.

La carga de materiales envía los archivos originales (`.txt`, `.md` y `.pdf`) como
`multipart/form-data` a `POST /api/v1/courses/{courseId}/documents`.

## Ejecutar

```bash
# desde ./webapp
npm install
npm run dev
```

`npm run build` compila TypeScript y genera la aplicación de producción en `dist/`.

## Rutas

- `/`: panel de aprendizaje y cursos disponibles.
- `/cursos/:courseId`: chat y contexto del curso, por ejemplo `/cursos/calculo-i`.
- `/cursos/:courseId/grafo`: visualización del grafo del curso.
- `/materiales?courseId=:courseId`: biblioteca filtrada por curso.
- `/curso/:courseId`: alias compatible con la ruta singular anterior.

## Organización

```text
src/
  components/   módulos visuales reutilizables: chat, grafo, materiales y layout
  contexts/     estado compartido de datos y tema
  data/         respuestas JSON de demostración
  pages/        composición de las rutas
  services/     interfaz API e implementación simulada
  types/        contratos TypeScript compartidos
docs/            contrato de datos
```

Las páginas componen la disposición; `KnowledgeGraph`, `MessageRenderer`, `ChatComposer` y los componentes de materiales mantienen responsabilidades acotadas para poder reubicarlos o reutilizarlos. Catppuccin Latte/Mocha sigue por defecto la preferencia del sistema operativo y permite guardar una elección manual.
