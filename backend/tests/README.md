
## Ejecutar test_ai_gateway.py

1. Copia el .env.example como .env y deja vacías las keys de proveedoresi

```
   OPENAI_API_KEY=
   GEMINI_API_KEY=
```
1. Levanta el gateway (recrea litellm para que tome el .env)

```
docker compose up -d --force-recreate ollama litellm`
```

1. Ejecuta las pruebas

```
python -m unittest backend.tests.test_ai_gateway -v
```

## RNF-S-1 · Autenticación de rutas protegidas

`fixtures/rnf_s1.json` clasifica las operaciones de la API persistente
por método y ruta. La prueba de inventario comprueba que todas las
operaciones descritas en OpenAPI estén clasificadas y que las protegidas
declaren el esquema Bearer. Revisa la clasificación cuando se agregue o
cambie una ruta.

Cada ruta protegida se prueba sin token y con un Bearer malformado. Se
exige HTTP 401, `WWW-Authenticate: Bearer`, el código `INVALID_SESSION`
y que no se abra PostgreSQL. El alcance de esta matriz es autenticación
sin credenciales válidas en la API. Las sesiones de formato válido
pero revocadas o vencidas se verifican con pruebas de sesión separadas.

Desde la raíz, ejecuta solo este módulo en la imagen de pruebas:

```bash
docker compose --profile test build backend-test
docker compose --profile test run --rm --no-deps backend-test \
  python -m pytest -q -p no:cacheprovider tests/test_rnf_s1.py
```

El cliente de pruebas ejecuta FastAPI en el proceso de pytest. El comando
no levanta TutorIA ni la base de datos. Para pytest local, necesitas el
entorno con las dependencias declaradas y puedes ejecutar
`python -m pytest -q backend/tests/test_rnf_s1.py`.

## RNF-S-2 · Autorización por rol, recurso y acción

`fixtures/rnf_s2.json` define 9 acciones para tres perfiles: coordinación,
estudiante activo y usuario autenticado externo. El denominador es de
27 casos en cursos, membresías y documentos. Se prueba la diferencia
entre borrar un documento propio y uno subido por otra persona. Los
casos denegados exigen el código exacto y un estado sin cambios observado
desde la sesión de coordinación. Los casos permitidos verifican el
recurso devuelto o el cambio esperado.

Desde la raíz, ejecuta el módulo con el PostgreSQL temporal del perfil
`test`:

```bash
docker compose -p tutoria-rnf-s2-test --profile test build test-db-init backend-test &&
docker compose -p tutoria-rnf-s2-test --profile test run --rm backend-test \
  python -m pytest -q -p no:cacheprovider tests/test_rnf_s2.py
```

El cliente ejecuta la API dentro de pytest, con datos sintéticos y
rollback por caso. No usa la base principal ni levanta la aplicación web.
La matriz describe este alcance y puede ampliarse a otros recursos.

## RNF-S-3 · Privacidad de perfiles y materiales

`fixtures/rnf_s3.json` cruza ocho acciones de lectura con el titular,
un compañero de clase y un usuario de otra clase. Son 24 casos HTTP.
Además, tres casos de base de datos verifican que el rol de API solo
puede leer su propia fila de usuario y no puede seleccionar el hash de
contraseña. La prueba distingue campos compartidos en el directorio
de la clase, documentos de curso y material generado privado.

Desde la raíz, usa el PostgreSQL temporal de un proyecto de pruebas
independiente:

```bash
docker compose -p tutoria-rnf-s3-test --profile test build test-db-init backend-test &&
docker compose -p tutoria-rnf-s3-test --profile test run --rm backend-test \
  python -m pytest -q -p no:cacheprovider tests/test_rnf_s3.py
```

FastAPI se ejecuta dentro de pytest y los datos sintéticos se revierten
por caso. No se usa la base principal ni se levanta la aplicación web.
