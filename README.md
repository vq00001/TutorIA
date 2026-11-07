# TutorIA

React + FastAPI + PostgreSQL 18/pgvector. Los originales se guardan en PostgreSQL; la cola SQL y un worker publican versiones completas. Ollama es el proveedor implementado.


## Preparación local

Se requiere Docker Desktop con motor Linux. Python 3.12 y uv permiten ejecutar las herramientas locales.

1. Ejecutar `./scripts/init-dev-env.ps1` en PowerShell. Crea `.env` de desarrollo con credenciales aleatorias y sin sobrescribir un archivo existente. Para un despliegue, proporcionar configuración y secretos del entorno; no usar estas credenciales como datos de producción.
2. Ejecutar `docker compose up -d db ollama mail` y `docker compose run --rm migrate`. Las migraciones son la única vía de creación/evolución de estructura. No hay `create_all` al arrancar la API.
3. Instalar los modelos elegidos explícitamente:

   ```powershell
   docker compose exec ollama ollama pull all-minilm
   docker compose exec ollama ollama pull qwen3:1.7b
   docker compose run --rm -e OLLAMA_URL=http://ollama:11434 -e MODEL_EMBED=all-minilm -e MODEL_LLM=qwen3:1.7b migrate python -m backend.db.configure_embedding
   ```

4. Copiar los valores emitidos `EMBEDDING_SPACE_ID` y `MODEL_LLM_REVISION` a `.env`. La dimensión se descubre mediante una petición real; no se deduce del nombre del modelo. Conservar las revisiones instaladas al reproducir el entorno.
5. Ejecutar `docker compose up -d --build api worker scheduler webapp`. Abrir [TutorIA local](http://localhost:8080). El buzón SMTP local se consulta en [Mailpit](http://localhost:8025). Registro y recuperación envían enlaces a ese buzón en desarrollo.

`APP_ORIGIN` debe coincidir exactamente con el origen del navegador. En HTTPS las cookies son Secure; en desarrollo HTTP se utiliza el origen local explícito. El proxy inverso de producción debe terminar TLS y preservar el origen configurado. No se publican los puertos SQL ni Ollama al host.

La API rechaza el arranque con configuración obligatoria ausente o una revisión Alembic distinta de la esperada. Los cursos nuevos reciben el espacio configurado. TXT, MD y MARKDOWN requieren UTF-8, hasta 30.000.000 bytes por archivo y diez archivos por lote. PDF/DOCX/PPTX no se anuncian ni admiten todavía.

## Pruebas existentes

```powershell
uv sync --project backend --frozen
./scripts/test.ps1 --prepare
./scripts/test.ps1 --suite smoke
```

Para incluir la interfaz y el recorrido completo del navegador con servicios sintéticos:

```powershell
./scripts/test.ps1 --prepare --with-browser
./scripts/test.ps1 --suite smoke --with-browser
```

`--prepare` construye herramientas inmutables, separadas de las ejecuciones. Cada smoke obtiene proyecto/cluster/volumen/red/UUID y credenciales nuevos. No publica puertos, no utiliza la base principal y verifica limpieza. La imagen debe coincidir con el contenido actual de backend y tests; después de editarlo hay que repetir `--prepare`.

El wrapper equivalente POSIX es `./scripts/test`, con el entorno Python creado en Linux. `TUTORIA_TEST_DOCKER_CONTEXT` permite elegir un contexto Docker explícito; por defecto usa `desktop-linux`.

`--suite unit` y `--suite integration` limitan el alcance del smoke. El comando sin `--suite` incluye navegador y mantiene un gate fallido mientras falten las suites obligatorias. `full` requiere un ejecutor desechable explícitamente designado y sigue sujeto a esa matriz incompleta; live-ai y performance todavía abortan con código 2. Códigos: 0 pruebas elegidas aprobadas y limpieza verificada; 1 fallo de prueba; 2 infraestructura/configuración o suites faltantes; 3 limpieza no verificada. `./scripts/test-clean.ps1 --run-id <UUID>` recupera únicamente recursos identificados como propios de una ejecución abandonada; `./scripts/test.ps1 --reconcile` busca ejecuciones abandonadas del controlador.

## Componentes

- `backend/api`: `/api/v1`, sesiones, permisos, materiales, chat, grafo, prácticas, dominio, rutas y actividad.
- `backend/db`, `backend/migrations`: transacciones con identidad local y migraciones del esquema `app`.
- `backend/workers`: ingesta, generaciones, corrección, extracción, purga y scheduler.
- `backend/repositories/vector.py`: recuperación exacta, sin HNSW activado.
- `Webapp`: adaptador HTTP con cookies/CSRF y cargas reales. Las preguntas y soluciones ya no se generan en el navegador.
- `docker/compose.test.yaml`, `scripts/test_runner.py`: harness de smoke aislado.

El volumen `deletion_ledger` es independiente de los respaldos de la base, pero un volumen por sí solo no es un respaldo externo. Ya se incorporaron borrado de cuentas/cursos, retención y herramientas de respaldo/restauración con pruebas PostgreSQL. Quedan programación, copia externa y ensayo operativo en el entorno objetivo, además de completar la matriz de aceptación.
