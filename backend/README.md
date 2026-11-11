# Backend de TutorIA

La persistencia se organiza en [db/](db/). El prototipo de procesamiento permite previsualizar documentos mientras se conecta la ingestión y la búsqueda a PostgreSQL.

```text
backend/
├── db/
│   ├── connection.py        # Transacciones y contexto del usuario
│   ├── initialize.py        # Instala el esquema directamente
├── API/                     # Contrato OpenAPI
└── experiments/             # Calibración y datos de evaluación
```

## Base de datos local

Desde la raíz del proyecto:

```bash
docker compose up -d --build db db-init
docker compose wait db-init
```

Compose inicia PostgreSQL, crea los roles locales y habilita pgvector. El instalador también prepara el esquema vacío `app` para las funcionalidades posteriores.

Usa un volumen nuevo para instalar el esquema desde cero. Volver a levantar Compose reutiliza un esquema ya creado por este instalador. La conexión local predeterminada está en `.env.example`. Configura `DATABASE_URL` con el rol de aplicación y usa `backend.db.connection.transaction(user_id)` para consultar con el contexto del usuario.

## Previsualizar documentos

Desde la raíz, instala el entorno del backend y ejecuta:

```bash
make -C backend install
make -C backend preview DOCS=/ruta/al/documento.md
```

La ingestión y la búsqueda se conectarán a PostgreSQL en cambios posteriores.
