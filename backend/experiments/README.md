# Experimentos

Ejecuta `make -C backend calibrate` desde la raíz para estimar `TOKEN_ESTIMATE_RATIO` con Ollama. Instala primero el entorno con `make -C backend install`.

El corpus y los casos de recuperación están en [retrieval/data/](retrieval/data/). El ejecutor se podrá reconstruir cuando el flujo de recuperación use PostgreSQL.

Las pruebas aisladas de extracción, consolidación y prerrequisitos del grafo
están en [knowledge_graph/](knowledge_graph/); las recomendaciones basadas en
notas simuladas están en [learning_gaps/](learning_gaps/).
