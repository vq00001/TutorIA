# Experimentos de TutorIA

Cada experimento tiene sus parámetros en `config/<id>.toml`. El lanzador
`ejecutar.py` corre un experimento o todos en orden. Los scripts de arranque y
la configuración versionable de Docker están en `infra/`. El YAML de
compilación generado queda en `instancias/temp/`, que Git ignora.

## Ejecución

Desde la raíz de TutorIA, con Docker Compose y Python 3.11 o posterior:

```bash
python3 experimentos/ejecutar.py rnf-r1  # Latencia del tutor
python3 experimentos/ejecutar.py rnf-r3  # Procesamiento de documentos
python3 experimentos/ejecutar.py todos   # Todos los registrados
```

El lanzador lee el TOML de cada experimento. Con
`arrancar_servicios = true`, detiene los contenedores anteriores del
proyecto de prueba y arranca PostgreSQL con un volumen nuevo por fase.
Así se empieza con una cola vacía, incluso en corridas consecutivas de
un mismo experimento o entre las fases CPU y GPU.

El nombre del volumen aparece en consola y en el JSON del experimento.
Los volúmenes de corridas anteriores se conservan, incluido el volumen
compartido anterior. También se conservan los modelos y los informes.
Con el reinicio habilitado, el lanzador detiene los contenedores al terminar,
incluso si la corrida falla, y la siguiente fase vuelve a levantarlos.

Puedes usar `reiniciar_servicios = false` en `[ejecucion]` para dejar los
contenedores activos al terminar. El próximo experimento gestionado
igualmente arrancará con un volumen nuevo. Con `arrancar_servicios = false`,
se usa la instancia externa tal como está y su cola queda bajo tu control.
El comando que solo prepara servicios usa el volumen compartido.

Al inicio de cada fase, el lanzador muestra el procesador, cómo se
obtendrán los servicios y los parámetros principales del experimento.
Con `both`, muestra CPU en la fase 1/2 y GPU en la fase 2/2.

Para preparar solo los servicios habilitados, usa
`bash experimentos/infra/arrancar-todos.sh`. Con `both` ese comando solo
prepara la primera fase (CPU). Para inspeccionar o detener RAG,
usa `bash experimentos/infra/arrancar-rag.sh ps`, `logs` o `down`.

## RNF-R-1 · Latencia del tutor con RAG

En `config/rnf-r1.toml`, cambia `modo` a `"piloto"` para ejecutar únicamente
el piloto. Elige `procesador = "cpu"` (predeterminado), `"gpu"` o `"both"`.
Con `both` y `arrancar_servicios = true`, el lanzador hace primero la
corrida en CPU y luego cambia la configuración de Ollama y hace la corrida
en GPU. Cada fase tiene su piloto y su propio JSON, con el procesador en
el nombre. Si Docker indica que la GPU NVIDIA no está disponible o que
falta configurarla, la fase GPU se marca como `[OMITIDO]` y el lanzador
continúa. El aviso aparece en amarillo en terminales interactivas.

Al gestionar el arranque, el lanzador comprueba después del piloto que
Ollama ejecutó `qwen3:1.7b` en CPU o con uso de GPU, según corresponda.
Si la fase GPU detecta que Ollama cargó el modelo solo en CPU, también
se omite y queda registrado en el informe del piloto.
Con `arrancar_servicios = false` puedes reutilizar una instancia para un
solo modo. En ese caso el informe registra el procesador solicitado.

El criterio, el escenario y las preguntas aparecen antes de los tiempos
de sondeo. Las rutas relativas parten de la raíz del repositorio.

La corrida usa la cuenta local de desarrollo y crea un curso de prueba.
Si la cuenta es personalizada, usa las variables de entorno
`DEV_ACCOUNT_EMAIL` y `DEV_ACCOUNT_PASSWORD`.

El piloto queda fuera de las 20 consultas iniciales. Se cronometra desde
el envío hasta recibir la respuesta por la API. Una respuesta cuenta si
incluye una fuente del documento cargado. El p90 se calcula por rango más
próximo y los fallos y las respuestas sin fuente se colocan al final. Si
el rango cae en un fallo, el p90 global se guarda como `null`. El p90 de las
respuestas con fuente se incluye como dato de diagnóstico.

Cada corrida escribe un JSON nuevo en `resultados/` y lo actualiza después
de cada medición. Los JSON quedan fuera de Git. Código de salida 0 si
las fases ejecutadas cumplen (o el piloto recupera una fuente), 1 si
alguna no cumple y 2 si alguna se interrumpe o falla el piloto. Las
omisiones por falta de GPU no afectan el código de salida. Un error se
registra y el lanzador continúa con las fases y experimentos restantes.

## RNF-R-3 · Procesamiento de un documento de 30 MB

```bash
python3 experimentos/ejecutar.py rnf-r3
```

La configuración `config/rnf-r3.toml` genera un Markdown de **30.000.000
bytes** a partir de los apuntes de Cálculo III. Puedes indicar un archivo
propio que pese exactamente `tamano_bytes`. La corrida crea un curso de
prueba y guarda su informe en `resultados/`.

La medida principal va desde que el cliente recibe la confirmación
de la carga hasta que observa `ready`, estado que el backend publica
al dejar los fragmentos y embeddings disponibles. El objetivo se cumple
en **300 s o menos**. El informe guarda por separado el tiempo de carga,
el tamaño real, el hash del archivo y los cambios de estado observados.
Una carga rechazada, un procesamiento fallido o un plazo agotado no
cumplen el criterio. Un resultado de Markdown representa ese formato
y ese contenido.

La API admite 30 MB para un solo archivo. Si envías varios archivos en
una petición, el límite agregado sigue siendo de 20 MiB. Con
`arrancar_servicios = false`, usa una instancia que ya incluya el límite
actualizado tanto en la API como en el proxy.

## Experimentos pendientes

[RNF-R-2](pendientes/rnf-r2/README.md) queda fuera del lanzador hasta que
TutorIA tenga un worker que consuma `generate_exam`.

## Agregar otro experimento

1. **Implementación.** Crea `instancias/rnf_ejemplo.py` con una función
   `run(config, repo, results_dir, *, processor, manages_services)`.
   Recibe el TOML como diccionario, la raíz del repositorio, el directorio
   de resultados y el procesador de la fase. Guarda los informes en
   `results_dir` con un nombre único por corrida. Devuelve 0, 1 o 2 según
   el resultado de la prueba.
2. **Parámetros.** Crea `config/rnf-ejemplo.toml`. Pon `[ejecucion]` primero
   con `arrancar_servicios = true` o `false` y, si aplica, `procesador`
   con `"cpu"`, `"gpu"` o `"both"`. Luego agrega el criterio, los datos de
   prueba y los parámetros técnicos. Mantén las credenciales en el entorno.
3. **Resumen en consola.** Define `resumir_configuracion(config)` en el
   mismo módulo. Devuelve un diccionario con etiquetas legibles y los
   valores principales, por ejemplo el umbral o el número de casos.
   El lanzador llama a `mostrar_configuracion` para imprimir ese resumen
   junto con el procesador y el arranque de cada fase. Omite credenciales
   y otros datos sensibles del diccionario.
4. **Arranque.** Si necesitas servicios, reutiliza un script de `infra/`
   o agrega uno que acepte `up` y termine cuando estén listos. También
   debe aceptar `down` para detener los servicios de prueba y conservar
   sus volúmenes. Si el stack usa PostgreSQL, debe usar `TUTORIA_DB_VOLUME`
   como nombre del volumen de datos para aislar la cola. Guarda cualquier YAML generado en `instancias/temp/`. Usa `None` en el
   registro si la prueba no tiene script de arranque.
5. **Registro.** Importa el módulo en `ejecutar.py` y agrega al diccionario
   `EXPERIMENTS` el ID que coincide con el nombre del TOML:

   ```python
   "rnf-ejemplo": (
       rnf_ejemplo.run,
       rnf_ejemplo.resumir_configuracion,
       "arrancar-rag.sh",
   ),
   ```

   Ejecuta solo la nueva prueba con
   `python3 experimentos/ejecutar.py rnf-ejemplo`. El comando con `todos`
   la incluirá en el orden del registro.
