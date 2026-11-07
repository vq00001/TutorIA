.PHONY: up dev down

up:
	docker compose --profile prod up --build

dev:
	docker compose --profile dev up --build

down:
	docker compose down
