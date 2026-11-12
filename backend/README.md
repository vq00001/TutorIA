# Backend de TutorIA

La persistencia se organiza en [db/](db/). El prototipo de procesamiento permite previsualizar documentos mientras se conecta la ingestión y la búsqueda a PostgreSQL.

```text
backend/
├── db/
│   ├── connection.py        # Transacciones y contexto del usuario
│   ├── initialize.py        # Prepara la extensión y los módulos
│   └── users.py             # Tabla y consulta del perfil propio
├── API/                     # Contrato OpenAPI
└── experiments/             # Calibración y datos de evaluación
```

## Base de datos local

Desde la raíz del proyecto:

```bash
docker compose up -d --build db db-init
docker compose wait db-init
```

Compose inicia PostgreSQL, crea los roles locales y habilita pgvector. El instalador prepara el esquema `app` e instala el módulo de usuarios. La API puede leer el perfil del usuario asociado a la transacción. El hash de contraseña queda fuera de los permisos de lectura de la API.

Usa un volumen nuevo para instalar el esquema desde cero. Volver a levantar Compose reutiliza un esquema ya creado por este instalador. La conexión local predeterminada está en `.env.example`. Configura `DATABASE_URL` con el rol de aplicación y usa `backend.db.connection.transaction(user_id)` para consultar con el contexto del usuario.

## Previsualizar documentos

Desde la raíz, instala el entorno del backend y ejecuta:

```bash
make -C backend install
make -C backend preview DOCS=/ruta/al/documento.md
```

La ingestión y la búsqueda se conectarán a PostgreSQL en cambios posteriores.

## Pruebas

Desde la raíz, ejecuta `make -C backend test` después de instalar las dependencias. Las pruebas unitarias simulan la conexión.

Para probar PostgreSQL, levanta primero la base local y espera a que termine `db-init`. Usa una base de desarrollo con el esquema instalado. Las pruebas inspeccionan la extensión y los roles y prueban la política de usuarios con datos sintéticos que se revierten al final.

```bash
TUTORIA_TEST_ADMIN_URL='postgresql+psycopg://tutoria_migrator:tutoria_local_migrator@127.0.0.1:55432/tutoria' \
  make -C backend test
```

## Registro de usuarios

Configura `DATABASE_URL` con el rol de la API. Si todavía no tienes `.env`, puedes copiar
`.env.example`. Con PostgreSQL iniciado y `db-init` completado, ejecuta desde la raíz:

```bash
uv run --project backend uvicorn backend.http_api:app
```

El endpoint `POST /api/v1/auth/register` recibe `email`, `password` y `displayName`.
Devuelve la cuenta pendiente con un nickname asignado. Un correo repetido devuelve
`409`. La contraseña se guarda con Argon2id y la respuesta solo contiene los datos
de la cuenta. Para habilitar el registro en una base ya creada, reconstruye y
ejecuta el inicializador con `docker compose up -d --build db db-init` y espera con
`docker compose wait db-init`.

## Inicio de sesión

`POST /api/v1/auth/login` recibe `email` y `password`. Las cuentas pendientes y
activas pueden autenticarse. Las cuentas pendientes conservan su estado para el
flujo de verificación de correo. La respuesta incluye un token Bearer válido
durante una hora y PostgreSQL guarda solamente su hash. Las cuentas suspendidas
o pendientes de borrado reciben el mismo `401` que las credenciales incorrectas.

Reconstruye y ejecuta `db-init` para crear la tabla de sesiones y los permisos
sobre una base ya iniciada.

## Perfil propio y cierre de sesión

Envía el token devuelto por el inicio de sesión en `Authorization: Bearer <token>`.

- `GET /api/v1/users/me` devuelve el perfil asociado a la sesión y la fecha de verificación del correo, si existe.
- `POST /api/v1/auth/logout` revoca la sesión usada y responde `204`. Los demás tokens de esa cuenta siguen vigentes.
- Un token desconocido, caducado o revocado, o una cuenta suspendida o pendiente de borrado, recibe `401`.

Reconstruye y vuelve a ejecutar `db-init` para instalar estas operaciones sobre una base ya creada.

## Verificación de correo y recuperación de contraseña

`db/account_tokens.py` ofrece las operaciones de persistencia. El inicializador
crea la tabla y sus permisos al volver a ejecutar `db-init`.

- `issue_account_token(conn, email, purpose)` reemplaza el token anterior del
  mismo propósito. Devuelve el token al servicio que se encargue de enviarlo.
  Confirma la transacción antes del envío. PostgreSQL guarda solo el hash.
- `verify_email(conn, token)` activa una cuenta pendiente. El token dura 24 horas.
- `reset_password(conn, token, password_hash)` acepta un hash Argon2id, consume el
  token de 30 minutos y elimina todas las sesiones de la cuenta.

El envío de correo y las rutas HTTP quedan fuera de este bloque. Al integrarlos,
la solicitud de un token debe responder igual exista o no una cuenta para el correo.

## Clases y membresías

`db/courses.py` instala las clases y su relación N:M con los usuarios. Una cuenta
activa puede crear una clase y queda como coordinadora en la misma transacción.
Los miembros activos consultan sus clases y los datos públicos del resto del
curso. Solo la coordinación puede editar la clase o modificar membresías. Los
cambios de configuración y membresía requieren la versión de la fila que se leyó.
Una clase activa o archivada conserva al menos una membresía coordinadora activa.

Usa estos repositorios dentro de `transaction(user_id)`. Reconstruye y ejecuta
`db-init` para instalar el módulo en una base ya iniciada. Los roles persistidos
siguen el ZIP original, `student` y `coordinator`. La integración HTTP, las
invitaciones y el borrado físico quedan para sus propios cambios.

## Invitaciones directas

`db/invitations.py` permite a la coordinación invitar por correo a una cuenta
activa. La operación responde igual si el correo no existe, no es elegible o ya
pertenece a la clase. Las invitaciones duran siete días. Repetir una invitación
vigente conserva su ID y su vencimiento.

La cuenta destinataria puede listar sus invitaciones pendientes y aceptarlas o
rechazarlas. Aceptar crea o reactiva su membresía como `student` en la misma
transacción, respetando el rol si ya tiene una membresía activa. Renovar una
invitación vencida o rechazada genera otro ID.

Usa estos repositorios dentro de `transaction(user_id)` y vuelve a ejecutar
`db-init` para instalar el módulo. Las rutas HTTP y las notificaciones se
integrarán por separado.

## Documentos

`db/documents.py` adapta las tablas de documentos, metadatos del archivo y
contenido del ZIP. `register_upload` guarda el archivo original en PostgreSQL y
calcula SHA-256 sobre sus bytes. Si la clase ya tiene ese hash, emite
`DuplicateDocumentWarning` y devuelve `None`. Un archivo con bytes distintos
se registra como otro documento, incluso si tiene el mismo nombre. La fila
inicial de `document_versions` sirve al procesamiento interno.

`list_documents` lista los metadatos visibles para los miembros de la clase,
con cursor por fecha e identificador. Las cargas se permiten en clases activas.
El listado también funciona en clases archivadas. Usa ambos repositorios dentro
de `transaction(user_id)` y vuelve a ejecutar `db-init` para instalar el módulo.
`download_document` devuelve los bytes originales y los metadatos de descarga
a los miembros activos de una clase visible. El archivo está disponible desde
la carga. Los documentos retirados o archivos purgados quedan fuera.

`delete_document` permite a quien subió el archivo o a la coordinación borrar
el documento de una clase activa. La operación elimina el archivo y sus metadatos,
libera el hash y actualiza la revisión del corpus en la misma transacción.
Las clases archivadas se mantienen en modo lectura. El borrado también elimina
los derivados de procesamiento. Las operaciones de documentos se exponen como
repositorios de base de datos.

## Procesamiento y embeddings

`db/processing.py` reutiliza las ejecuciones, fragmentos y espacios de
embeddings del ZIP. El administrador registra el espacio con
`register_embedding_space`. El worker usa `transaction(user_id)` con una cuenta
activa de la clase y llama a `start_processing`, `get_processing_source` y
`publish_processing`, o a `fail_processing` si falla el procesamiento.

El resultado incluye el texto extraído exacto. Los hijos usan intervalos
`[inicio, fin)` de caracteres Unicode dentro de ese texto. PostgreSQL comprueba
que el intervalo reproduce el hijo y que cada vector normalizado tiene la
dimensión del espacio. Publicar guarda padres, hijos y vectores juntos. Un
fallo de reprocesamiento conserva el resultado ya publicado.

El borrado del documento elimina también sus ejecuciones, fragmentos y vectores.
Vuelve a ejecutar `db-init` para instalar el módulo en una base existente.
La extracción y las llamadas al modelo se integran desde el proceso externo.

## Búsqueda vectorial

`db/vector_search.py` ofrece `search_course_chunks(conn, course_id,
embedding_space_id, vector, limit=4, threshold=0.5)`. Úsala dentro de
`transaction(user_id)` con el rol de la API. El vector debe corresponder al
espacio de embeddings indicado y la consulta se limita a las ejecuciones
publicadas de los documentos visibles en la clase.

La búsqueda calcula similitud coseno exacta, elige el mejor hijo por padre y
aplica el límite sobre los padres. Cada resultado incluye el texto del padre
que sirve de contexto, el hijo ganador y sus offsets Unicode `[inicio, fin)`,
junto con los IDs del documento, la ejecución y el espacio. Los empates se
resuelven por identificador. Vuelve a ejecutar `db-init` para instalarla en
una base existente. La API HTTP y el registro de fuentes de una generación
quedan para sus respectivos módulos.

## Registro de generaciones

`db/generations.py` separa la ejecución, cada solicitud al modelo y sus fuentes
ordenadas. Usa `start_generation` y `record_generation_request` dentro de
`transaction(user_id)` con el rol de la API. Pasa los mismos bytes UTF-8 que
usará el cliente HTTP para enviar el JSON de chat. El registro conserva
espacios, escapes y orden de claves. Confirma la transacción antes del envío
para conservar el intento aunque falle la llamada.

Pasa las fuentes que se incorporaron al mensaje, con los campos devueltos por
`search_course_chunks`.
PostgreSQL comprueba la clase, la ejecución publicada, la relación padre/hijo,
el espacio y los offsets. El propietario puede consultar su registro con
`get_generation_request`. Si se borra un embedding utilizado, se invalida la
generación y se eliminan sus solicitudes, que pueden contener texto del documento.
Vuelve a ejecutar `db-init` para instalar el módulo. El cliente del modelo y
las rutas HTTP se integrarán por separado.

## Conversaciones y mensajes

`db/conversations.py` conserva historiales privados por usuario y clase.
Usa `create_conversation`, `list_conversations` y `list_messages` dentro de
`transaction(user_id)`. Los mensajes se ordenan por secuencia, incluso si llegan
varias escrituras a la vez. Las clases archivadas permiten leer el historial.

`append_user_message` guarda las partes estructuradas de la pregunta.
`append_assistant_message` requiere esa pregunta y una generación de chat de
la misma cuenta y clase con una solicitud registrada. Guardar la respuesta
completa la generación en la misma transacción. Si se invalida la generación
al borrar una fuente, PostgreSQL retira el contenido de la respuesta y conserva
su lugar en el historial.

Vuelve a ejecutar `db-init` para instalar el módulo. Las rutas HTTP y el cliente
del modelo se integrarán por separado.

## Citas de respuestas

`db/citations.py` ofrece `append_cited_assistant_message` para guardar una
respuesta y sus citas en una sola transacción. Indica el ID de la solicitud al
modelo que produjo la respuesta. Cada cita identifica una fuente por su
`rank` dentro de esa solicitud. Las respuestas sin citas pueden seguir usando
`append_assistant_message`.

`list_message_citations` devuelve las citas en el orden registrado, con los
IDs del padre y del hijo y sus offsets `[inicio, fin)`, tomados directamente
de la fuente. Si la fuente se elimina, queda un marcador `source_removed`
sin el enlace ni los offsets. El historial sigue siendo privado para su
propietario. Vuelve a ejecutar `db-init` para instalar el módulo.

## Materiales generados

`db/generated_materials.py` guarda resúmenes, tarjetas y explicaciones como
resultados de una generación con una solicitud registrada. Cada material tiene
un título, un tipo, un objeto JSON y una versión positiva del formato.
`store_generated_materials` guarda el lote en una transacción. Al reintentar,
conserva los IDs de los materiales para evitar duplicados.

`list_generated_materials` y `get_generated_material` consultan los materiales
del propietario de la generación en la clase indicada. Si se invalida la
generación, PostgreSQL sustituye el título por «Contenido retirado» y limpia el
contenido, conservando el vínculo y la versión del esquema. El módulo se
instala al volver a ejecutar `db-init`.
