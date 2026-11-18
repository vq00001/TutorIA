.DEFAULT_GOAL := help

PYTHON ?= uv run python
MODULE := cli.main
EXPERIMENTS := experiments

.PHONY: help install prod sync lint format test preview calibrate

help:
	@echo "Comandos disponibles:"
	@echo "  make install       Instala dependencias de producción y desarrollo"
	@echo "  make prod          Instala solo dependencias de producción"
	@echo "  make sync          Sincroniza el entorno desde uv.lock"
	@echo "  make lint          Ejecuta Ruff"
	@echo "  make format        Formatea el código"
	@echo "  make test          Ejecuta tests"
	@echo "  make preview DOCS=docs"
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

calibrate:
	$(PYTHON) -m $(EXPERIMENTS).token_estimation.calibrate --output
