.PHONY: up dev up-nvidia dev-nvidia down

up:
	docker compose --profile prod up --build

dev:
	docker compose --profile dev up --build

up-nvidia:
	docker compose -f compose.yaml -f compose.nvidia.yaml --profile prod up --build

up-nvidia-local:
	WEBAPP_PORT=8082 docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.dev-account.yaml --profile prod up --build

dev-nvidia:
	docker compose -f compose.yaml -f compose.nvidia.yaml --profile dev up --build

down:
	docker compose down