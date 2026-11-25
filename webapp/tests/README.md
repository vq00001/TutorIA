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
| Frontend | 13 | Páginas omitidas y respuesta nueva fuera de la primera página |
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
