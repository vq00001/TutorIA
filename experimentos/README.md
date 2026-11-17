# Experimentos de TutorIA

Cada experimento tiene sus parámetros en `config/<id>.toml`. El lanzador
`ejecutar.py` corre un experimento o todos en orden. Los scripts de arranque y
la configuración versionable de Docker están en `infra/`. El YAML de
compilación generado queda en `instancias/temp/`, que Git ignora.

## Ejecución

Desde la raíz de TutorIA, con Docker Compose y Python 3.11 o posterior:

```bash
python3 experimentos/ejecutar.py rnf-r1  # Latencia del tutor
python3 experimentos/ejecutar.py rnf-r3    # Procesamiento de documentos
python3 experimentos/ejecutar.py rnf-r4    # Límite de carga por el proxy
python3 experimentos/ejecutar.py rnf-ci-2 # Compatibilidad de formatos
python3 experimentos/ejecutar.py rnf-s-5  # Aislamiento del tutor entre clases
python3 experimentos/ejecutar.py rnf-ac-2 # Recuperación desde un respaldo
python3 experimentos/ejecutar.py rnf-ac-3 # RPO de respaldos existentes
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

## RNF-R-4 · Test de integración del tamaño máximo

```bash
python3 experimentos/ejecutar.py rnf-r4
```

El test envía por el proxy web tres archivos de texto de **29.990.000**,
**30.000.000** y **30.010.000 bytes**, cada uno en una petición multipart
independiente. Los dos primeros deben devolver `202 Accepted` con un
documento y un trabajo de ingesta nuevos. El tercero debe devolver
`413` con el código de la API `UPLOAD_TOO_LARGE`. Un `413` genérico de
Nginx hace fallar el caso porque la petición no llegó a esa validación.

El arranque gestionado solo necesita Nginx, la API y PostgreSQL. El test
no espera la ingesta. Si usas una instancia existente, pon la URL pública
del proxy en `config/rnf-r4.toml`. El informe de `resultados/` conserva
el tamaño y la respuesta observada por caso.

## RNF-CI-2 · Compatibilidad de formatos

```bash
python3 experimentos/ejecutar.py rnf-ci-2
```

El experimento genera el mismo contenido en TXT, PDF y DOCX, con
Markdown como control, y usa un curso nuevo por formato. El código de
registro ficticio se genera en cada corrida y aparece en los documentos.
La pregunta no incluye el código. El PDF lleva un flujo de texto comprimido
para que leer sus bytes como texto no permita recuperar el dato.

El informe indica si cada archivo se aceptó, si llegó a `ready`, si el
tutor devolvió el código y si citó el documento cargado en ese curso.
TXT, PDF y DOCX deben pasar las cuatro comprobaciones. El control se
informa por separado y su fallo deja un código de salida distinto de cero.
Puedes cambiar el orden y los plazos en `config/rnf-ci-2.toml`.

El backend actual admite las cargas TXT, Markdown y PDF. DOCX puede
quedar rechazado y PDF puede fallar en un worker que solo decodifica UTF-8.
El experimento registra esas diferencias sin cambiar el soporte de formatos.

### Diagnóstico de PDF textual con TXT como control

```bash
python3 experimentos/ejecutar.py rnf-ci-2-pdf
```

`config/rnf-ci-2-pdf.toml` selecciona exactamente dos cursos, uno con PDF de
texto seleccionable y otro con TXT del mismo contenido. El JSON
`rnf-ci-2-pdf-*.json` distingue carga (HTTP y código de error), indexación
(transiciones y error del worker) y consulta (código recuperado y cita al
documento correspondiente). Una etapa bloqueada se registra como
`no_ejecutada`. El resultado se refiere a PDF textual con control TXT.
La corrida completa sigue disponible como `rnf-ci-2`.

## RNF-S-5 · Aislamiento entre clases en el tutor

```bash
python3 experimentos/ejecutar.py rnf-s-5
```

El experimento crea tres cuentas de prueba, cada una con un curso propio.
A y B reciben una ficha sobre el mismo proyecto con códigos ficticios
distintos. C queda sin material. El arranque local habilita el registro
de cuentas de prueba. Si usas una instancia existente, debe tener activada
la verificación automática de registros de desarrollo.

Se espera a que ambas fichas estén indexadas y se envía exactamente la
misma pregunta en los tres cursos. A y B deben recuperar y citar su propio
material sin devolver códigos ni citas ajenos. C debe entregar una respuesta
válida, sin ninguno de los códigos y sin citas. Un error del tutor hace
fallar el caso, incluso si no devolvió contenido ajeno.

El TOML `config/rnf-s-5.toml` permite cambiar el tema y los plazos. El JSON de
`resultados/` guarda las respuestas, las citas y las incidencias por curso.
Esta prueba cubre el recorrido del tutor RAG dentro del RNF-S-5.

## RNF-AC-2 · Recuperación de un curso

```bash
python3 experimentos/ejecutar.py rnf-ac-2
```

El experimento prepara un curso con una ficha Markdown y comprueba que
el tutor devuelve y cita su código ficticio antes del respaldo. Hace un
`pg_dump` de la base de prueba, detiene el origen y restaura en otro
contenedor PostgreSQL con su propio volumen. Los workers y la API del
destino se levantan después de que termina `pg_restore`.

Tras restaurar, la prueba abre una sesión nueva y comprueba que conserva
el curso y el documento indexado, que la descarga coincide byte a byte
por SHA-256 y que una consulta nueva del tutor devuelve el código y cita
el documento restaurado. El JSON registra ambas verificaciones, los
volúmenes y los tiempos de respaldo, restauración y recuperación.

El TOML `config/rnf-ac-2.toml` permite editar el caso y los plazos. Requiere
el arranque gestionado para aislar el origen y el destino. Los volúmenes y
el archivo `.dump` se conservan al terminar, junto al informe local en
`resultados/`. El respaldo contiene los datos de la instancia de prueba.

El veredicto cubre el curso y el material del escenario. La verificación
del 100 % de los datos respaldados y el RPO son pruebas aparte.

## RNF-AC-3 · RPO de 24 horas

```bash
python3 experimentos/ejecutar.py rnf-ac-3
```

En `config/rnf-ac-3.toml` indica el directorio con pares de informe
`rnf-ac-2-*.json` y archivo `.dump`. Por defecto la referencia es el
inicio de AC-3 en UTC. Para simular un incidente, fija una fecha y hora
con zona. La prueba solo considera archivos que ya estaban disponibles
en la referencia y evalúa respaldos existentes.

Comprueba el SHA-256 del archivo, restaura los candidatos en orden de
captura aproximado y verifica mediante el proxy que el curso y el
documento indexado son legibles y que la descarga coincide con el
original. Usa un proyecto Compose y un volumen PostgreSQL nuevos por
intento. Detiene los contenedores al terminar y conserva el volumen.
El puerto local 18080 debe estar libre.

AC-2 registra el intervalo de captura de `pg_dump` y cuándo quedó
disponible el archivo. AC-3 calcula los límites de antigüedad del último
punto recuperado. El JSON de salida queda en `resultados/rnf-ac-3-*.json`.

- `cumple` (0) si hay un punto restaurado con una antigüedad máxima de 24 h.
- `no_cumple` (1) si el punto restaurado supera 24 h incluso en el mejor
  extremo del intervalo y ningún candidato pendiente de verificar podría
  cambiar ese resultado.
- `indeterminado` (2) si falta evidencia suficiente, por ejemplo, si las
  fechas faltan, el intervalo cruza el umbral o falla la restauración por
  una causa sin determinar.

Los informes anteriores de AC-2 carecen de esas marcas. Para generar
un par compatible para el ensayo, corre AC-2 y luego AC-3 con los
archivos de ese ensayo. Un respaldo sintético recién generado aporta
evidencia sobre ese escenario. Evaluar el RPO operativo requiere los
respaldos históricos del entorno correspondiente.

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

## RNF-S-1 a S-4 · Informe de integración

Desde la raíz del repositorio:

```bash
bash experimentos/infra/ejecutar-seguridad.sh
```

Ejecuta los cuatro módulos RNF de pytest y las pruebas del registrador
(`test_audit.py`). Cada corrida crea un proyecto Compose nuevo con PostgreSQL
temporal del perfil `test`. FastAPI se ejecuta dentro de pytest. Al terminar,
el script retira los recursos de ese proyecto, incluso si pytest falla.

Cada corrida guarda su evidencia local en `resultados/rnf-seguridad-*/`.
Usa `resumen.json` para el conteo por estado y el detalle por caso,
`junit.xml` para consumir el informe con herramientas de CI y `pytest.log`
para los motivos y trazas. El JSON conserva la versión de Python y las
dependencias instaladas. `entorno.txt` registra el commit, el estado del
checkout y la huella de sus cambios versionados. Para repetir la medición
sobre la misma revisión, usa un checkout limpio y compara las dependencias.

Reporta por separado `passed`, `failed`, `error`, `xfailed`, `xpassed`,
`skipped` y `not_run`. Los fallos esperados de S-4 son brechas del alcance
evaluado. Un código de pytest 0 puede incluir `xfailed`. El denominador
corresponde a la selección de pruebas, incluidos los controles del
registrador. Si falla la construcción o el arranque y falta el resumen,
consulta `ejecucion.txt` y los logs para distinguir un problema de entorno
de un resultado de las pruebas.
