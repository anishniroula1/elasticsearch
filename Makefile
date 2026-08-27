SHELL := /bin/sh
COUNT ?= 10000
CSV ?= seed.csv
RESET ?= false
LIMIT ?=

.PHONY: help setup lock dev dev-os dev-es build run stop clean logs status seed seed-csv seed100k seed1m reset smoke

help:
	@echo "make setup       Create/update the local .venv with uv"
	@echo "make lock        Refresh uv.lock"
	@echo "make dev-os      Start only OpenSearch for local API development"
	@echo "make dev         Run FastAPI locally with uv and reload"
	@echo "make build       Build the API image"
	@echo "make run         Start OpenSearch, Dashboards, and API in Docker"
	@echo "make stop        Stop containers"
	@echo "make clean       Stop containers and delete local OpenSearch data"
	@echo "make logs        Follow API and OpenSearch logs"
	@echo "make status      Show container status"
	@echo "make seed        Seed COUNT fake records (default: 10000)"
	@echo "make seed-csv    Seed a CSV file (CSV=seed.csv RESET=false)"
	@echo "make seed100k    Reset and seed 100000 records"
	@echo "make seed1m      Reset and seed 1 million records"
	@echo "make reset       Recreate the index and seed COUNT records"
	@echo "make smoke       Call health and stats endpoints"

setup:
	uv sync

lock:
	uv lock

dev-os:
	docker compose up -d opensearch

# Keep the old command working for anyone who already uses it.
dev-es: dev-os

dev: setup
	OPENSEARCH_HOST=localhost \
	OPENSEARCH_PORT=9200 \
	OPENSEARCH_USE_SSL=false \
	OPENSEARCH_VERIFY_CERTS=false \
	OPENSEARCH_AUTH_MODE=none \
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

build:
	docker compose build

run:
	docker compose up -d --build --remove-orphans
	@echo "Swagger: http://localhost:8000/docs"
	@echo "OpenSearch Dashboards: http://localhost:5601"
	@echo "OpenSearch: http://localhost:9200"

stop:
	docker compose down

clean:
	docker compose down -v --remove-orphans

logs:
	docker compose logs -f

status:
	docker compose ps

seed:
	curl -fsS -X POST "http://localhost:8000/admin/seed?count=$(COUNT)&reset=false" | uv run python -m json.tool

seed-csv:
	curl -fsS -X POST -G \
		--data-urlencode "csvPath=$(CSV)" \
		--data-urlencode "reset=$(RESET)" \
		$(if $(LIMIT),--data-urlencode "count=$(LIMIT)",) \
		"http://localhost:8000/admin/seed" | uv run python -m json.tool

seed100k:
	curl -fsS -X POST "http://localhost:8000/admin/seed?count=100000&reset=true" | uv run python -m json.tool

seed1m:
	curl -fsS -X POST "http://localhost:8000/admin/seed?count=1000000&reset=true" | uv run python -m json.tool

reset:
	curl -fsS -X POST "http://localhost:8000/admin/seed?count=$(COUNT)&reset=true" | uv run python -m json.tool

smoke:
	curl -fsS http://localhost:8000/health | uv run python -m json.tool
	curl -fsS http://localhost:8000/stats | uv run python -m json.tool
