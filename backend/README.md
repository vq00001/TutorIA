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
