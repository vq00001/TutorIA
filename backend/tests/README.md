
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
