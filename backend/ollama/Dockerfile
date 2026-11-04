FROM ollama/ollama:latest

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV OLLAMA_HOST=0.0.0.0:11434

EXPOSE 11434

# Este HEALTHCHECK no valida que los modelos estén cargados y funcionando correctamente, solo que el servicio de Ollama está activo y puede responder a comandos.
HEALTHCHECK \
  --start-period=30s \
  --interval=10s \
  --timeout=5s \
  --retries=3 \
  CMD ollama list || exit 1

ENTRYPOINT ["/entrypoint.sh"]