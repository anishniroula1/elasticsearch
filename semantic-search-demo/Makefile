SHELL := /bin/sh

-include .env
export

SEED_FILE ?= data/seed.csv

.PHONY: help setup lock test dev init seed reset

help:
	@echo "make setup    Install Python dependencies with uv"
	@echo "make test     Run unit tests"
	@echo "make dev      Run the API with reload on port 8000"
	@echo "make init     Create the two AWS indexes"
	@echo "make seed     Recreate indexes and embed unique semantic text"
	@echo "make reset    Recreate both indexes without seed data"

setup:
	uv sync

lock:
	uv lock

test: setup
	uv run pytest -q

dev: setup
	uv run --locked uvicorn semantic_search.main:app --reload \
		--host 0.0.0.0 --port 8000

init: setup
	uv run --locked python -m semantic_search.cli init

seed: setup
	uv run --locked python -m semantic_search.cli seed "$(SEED_FILE)"

reset: setup
	uv run --locked python -m semantic_search.cli reset
