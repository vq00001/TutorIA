FROM ollama/ollama:latest

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV OLLAMA_HOST=0.0.0.0:11434

EXPOSE 11434

HEALTHCHECK \
  --start-period=60s \
  --interval=10s \
  --timeout=5s \
  --retries=3 \
  CMD ollama list | grep -q "qwen3:1.7b" && ollama list | grep -q "all-minilm" || exit 1

ENTRYPOINT ["/entrypoint.sh"]