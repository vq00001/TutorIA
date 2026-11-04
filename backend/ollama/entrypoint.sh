#!/bin/sh
set -e

ollama serve &
OLLAMA_PID=$!

until ollama list >/dev/null 2>&1; do
    sleep 2
done

ollama pull qwen3:1.7b
ollama pull all-minilm

wait "$OLLAMA_PID"