.DEFAULT_GOAL := help

PYTHON ?= uv run python
MODULE := main
EXPERIMENTS := experiments

.PHONY: help install prod sync lint format test preview ingest search \
	evaluate evaluate-limit calibrate

help:
	@echo "Comandos disponibles:"
	@echo "  make install       Instala dependencias de producción y desarrollo"
	@echo "  make prod          Instala solo dependencias de producción"
	@echo "  make sync          Sincroniza el entorno desde uv.lock"
	@echo "  make lint          Ejecuta Ruff"
	@echo "  make format        Formatea el código"
	@echo "  make test          Ejecuta tests"
	@echo "  make preview DOCS=docs"
	@echo "  make ingest DOCS=docs [CONTEXT=1]"
	@echo "  make search QUERY='pregunta' [ANSWER=1]"
	@echo "  make evaluate      Evalúa thresholds de recuperación"
	@echo "  make evaluate-limit Compara valores de LIMIT_QUERY"
	@echo "  make calibrate     Calibra TOKEN_ESTIMATE_RATIO"
	@echo "  make help          Muestra este mensaje de ayuda"

install:
	uv sync

prod:
	uv sync --no-dev

sync:
	uv sync --locked

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

test:
	$(PYTHON) -m pytest

preview:
	@test -n "$(DOCS)" || (echo "Uso: make preview DOCS=docs" && exit 1)
	$(PYTHON) -m $(MODULE) preview "$(DOCS)"

ingest:
	@test -n "$(DOCS)" || (echo "Uso: make ingest DOCS=docs" && exit 1)
	$(PYTHON) -m $(MODULE) ingest "$(DOCS)" $(if $(CONTEXT),--context)

search:
	@test -n "$(QUERY)" || (echo "Uso: make search QUERY='pregunta'" && exit 1)
	$(PYTHON) -m $(MODULE) search "$(QUERY)" $(if $(ANSWER),--answer)

evaluate:
	$(PYTHON) -m $(EXPERIMENTS).retrieval.run --reset --output

evaluate-limit:
	$(PYTHON) -m $(EXPERIMENTS).retrieval.evaluate_limit --reset --output

calibrate:
	$(PYTHON) -m $(EXPERIMENTS).token_estimation.calibrate --output
