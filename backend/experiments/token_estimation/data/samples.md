# Muestras de calibración

La ingesta transforma cada documento en fragmentos parent y child. Los children
se convierten en embeddings y se almacenan en Qdrant junto con su metadata.

Durante una consulta, el sistema genera el embedding de la pregunta, recupera
los children más similares, elimina duplicados por parent y entrega el contexto
completo al modelo de lenguaje para generar una respuesta fundamentada.

El ratio de tokens depende del tokenizer del modelo y del idioma del contenido.
Por eso esta muestra debe complementarse con textos reales del proyecto antes
de elegir un valor definitivo para TOKEN_ESTIMATE_RATIO.
