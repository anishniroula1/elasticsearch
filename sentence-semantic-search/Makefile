SHELL := /bin/sh

.PHONY: help setup test dev postgres local down logs clean

help:
	@echo "make dev    Build and run API and PostgreSQL"
	@echo "make postgres  Run only PostgreSQL in Docker"
	@echo "make local  Run only the API locally on port 8008"
	@echo "make test   Install development packages and run tests"
	@echo "make down   Stop Docker containers"
	@echo "make logs   Follow API logs"

setup:
	uv sync --extra dev

test: setup
	uv run pytest -q

dev:
	docker compose up --build

postgres:
	docker compose up -d postgres

local: setup
	uv run uvicorn sentence_search.main:app --reload --host 0.0.0.0 --port 8008

down:
	docker compose down

logs:
	docker compose logs -f api

clean:
	docker compose down --volumes
