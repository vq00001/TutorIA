
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
