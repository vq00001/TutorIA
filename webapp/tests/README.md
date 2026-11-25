# Regresiones del chat

Estas pruebas reproducen la pérdida de mensajes por paginación y la aceptación
silenciosa de respuestas terminadas por límite de longitud. Usan datos sintéticos
y respuestas HTTP simuladas.

Desde la raíz de TutorIA, con Node.js 22 y el entorno virtual del backend:

```bash
npm ci --prefix webapp
node --test webapp/tests/chat-pagination.test.cjs
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m pytest \
  -q -p no:cacheprovider backend/tests/test_chat_truncation.py
```

`npm ci` instala las dependencias fijadas en `webapp/package-lock.json`.
El test del frontend utiliza TypeScript, que ya es una dependencia del proyecto,
para compilar los servicios originales dentro de un contexto aislado de Node.
Las peticiones de los servicios se sustituyen por respuestas en memoria.

El backend necesita las dependencias de desarrollo de `backend/pyproject.toml`.
El test sustituye `requests.post` y bloquea cualquier otra petición HTTP.
La fixture PostgreSQL de la suite no se solicita.

| Selección | Casos | Defectos que se intentan reproducir |
| --- | ---: | --- |
| Frontend | 16 | Páginas omitidas y respuesta nueva fuera de la primera página |
| Backend | 6 | `finish_reason="length"` aceptado como respuesta completa |

Los casos del frontend incluyen 0, 20, 21 y 101 conversaciones, así como 0, 50,
51 y 101 mensajes por conversación. Se exige recuperar todos los IDs y el texto
original. Otros casos verifican la respuesta al último mensaje de conversaciones
largas, la conservación de texto largo y la comunicación de trabajos fallidos.

Los casos del backend incluyen respuestas completas, vacías, proveedores sin
`finish_reason` y dos respuestas terminadas con `finish_reason="length"`.
Con la interfaz actual de `chat_completion`, que devuelve texto y utiliza
`ValueError` para respuestas inválidas, se exige señalar el corte mediante ese
error. Una futura interfaz que exponga texto parcial y su estado requerirá
actualizar este contrato de prueba.

Los tests expresan el comportamiento esperado y fallan mientras persistan los
defectos. No se marcan como omitidos ni como fallos esperados.
La selección cubre los servicios del frontend y el adaptador del gateway.
El recorte visual por CSS y la generación real del modelo requieren pruebas
adicionales.

## Paginación compartida

`src/services/conversationApi.ts` expone `iterateConversationMessagePages`,
un iterador asíncrono que lee las páginas de una conversación en orden.
`databaseApi.getCourseMessages` consume todas las páginas para reconstruir
el historial. `waitForAssistant` termina el recorrido al encontrar la respuesta
al mensaje solicitado. Ambos conservan sus mensajes de error específicos.

El cargador de tests compila los módulos TypeScript locales y sustituye
únicamente `authorizedFetch`. Los controles adicionales verifican que la
búsqueda se detenga al encontrar la respuesta y que se comuniquen los errores
HTTP de una página posterior.

## RED de navegación al cerrar sesión

Desde la raíz de TutorIA, con Node.js 22 y las dependencias de npm instaladas:

```bash
node --test webapp/tests/logout-route.test.cjs
```

Para incluir las regresiones del chat:

```bash
node --test webapp/tests/chat-pagination.test.cjs webapp/tests/logout-route.test.cjs
```

El botón actual de cierre llama directamente a `authApi.logout`. Estos tests
compilan ese servicio con TypeScript y simulan HTTP, almacenamiento, eventos
y URL. El contrato esperado es terminar en `/`, sin los parámetros ni el
fragmento de la ruta anterior, incluso si falla el servidor o ya falta el token.
También se simula el login de otra cuenta después del cierre.

En `c73d767`, 5 controles pasan y 9 casos de navegación fallan. Los controles
verifican la limpieza del token y la notificación del cierre con HTTP 204,
HTTP 503, HTTP 401 y error de red, además del cierre desde el inicio.
El test permite varias notificaciones del cierre, tal como ocurre actualmente
con HTTP 401. La cantidad de notificaciones queda fuera de este contrato.

Son pruebas del servicio público usado por el botón, con navegador simulado.
La navegación real de React Router, el montaje de componentes y el botón Atrás
requieren una prueba adicional de interfaz. Si la futura solución ubica toda
la redirección en un componente, habrá que trasladar estas expectativas a ese
flujo de interfaz.

## RED de creación de cursos con materiales

Desde la raíz de TutorIA, con Node.js 22 y las dependencias de npm instaladas:

```bash
node --test webapp/tests/course-creation.test.cjs
```

Para incluir las regresiones de chat y cierre de sesión:

```bash
node --test webapp/tests/chat-pagination.test.cjs \
  webapp/tests/logout-route.test.cjs \
  webapp/tests/course-creation.test.cjs
```

El test compila y ejecuta `CreateCoursePage` y `AppDataProvider` originales.
Los callbacks de nombre, selección de archivos y envío se obtienen del árbol
JSX de la página. React se sustituye por un simulador mínimo de hooks y contexto.
La API es un doble en memoria con los métodos del contrato actual
`TutorIADataApi`. Cada caso empieza con un curso y un documento anteriores.

El contrato comprueba el estado final de la API simulada y del contexto,
la navegación y el estado de espera. Un fallo de carga o procesamiento debe
dejar solamente los cursos anteriores. Una carga aceptada en cola debe esperar
al procesamiento antes de navegar. La estrategia de creación provisional,
transacción o compensación queda abierta para GREEN.

Los 11 casos incluyen cinco controles que pasan en `e80b7e5`: nombre vacío,
creación sin archivos, material listo, rechazo al crear el curso y conservación
de los datos anteriores ante un fallo de carga.

Los seis casos RED cubren rechazo de carga, procesamiento fallido, lote con
un archivo listo y otro fallido, procesamiento pendiente, fallo posterior a la
aceptación y reintento de una carga rechazada. Los estados posteriores los
publica el doble de API, y el formulario actual no los espera.

Los tests mantienen las aserciones del resultado esperado y devuelven error
mientras persista el defecto. Una futura ampliación del contrato de API para
GREEN requerirá reflejar las operaciones nuevas en el doble de API según su
contrato real, conservando las aserciones de estado final.

La prueba cubre el flujo de callbacks y contexto con datos simulados.
La persistencia transaccional de PostgreSQL, el worker real, el DOM y el
comportamiento completo de React requieren pruebas de integración adicionales.
