SHELL := /bin/sh
COUNT ?= 10000
CSV ?= seed.csv
RESET ?= false
LIMIT ?=

.PHONY: help setup lock dev dev-es build run stop clean logs status seed seed-csv seed100k seed1m reset smoke

help:
	@echo "make setup       Create/update the local .venv with uv"
	@echo "make lock        Refresh uv.lock"
	@echo "make dev-es      Start only Elasticsearch for local API development"
	@echo "make dev         Run FastAPI locally with uv and reload"
	@echo "make build       Build the API image"
	@echo "make run         Start Elasticsearch, Kibana, and API in Docker"
	@echo "make stop        Stop containers"
	@echo "make clean       Stop containers and delete local Elasticsearch data"
	@echo "make logs        Follow API and Elasticsearch logs"
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

dev-es:
	docker compose up -d setup

dev: setup
	ELASTICSEARCH_URL=http://localhost:9200 \
	ELASTICSEARCH_USERNAME=admin \
	ELASTICSEARCH_PASSWORD=admin123 \
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

build:
	docker compose build

run:
	docker compose up -d --build
	@echo "Swagger: http://localhost:8000/docs"
	@echo "Kibana: http://localhost:5601 (admin / admin123)"
	@echo "Elasticsearch: http://localhost:9200"

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
