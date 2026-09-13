SHELL := /bin/sh

-include .env
export

SEED_FILE ?= data/seed.csv
SEED_RESET ?= true
SEED_SENTENCE_ENTITY_ID ?=
SEED_START_ARG = $(if $(strip $(SEED_SENTENCE_ENTITY_ID)),--sentence-entity-id "$(SEED_SENTENCE_ENTITY_ID)",)

.PHONY: help setup lock test dev init seed reset

help:
	@echo "make setup    Install Python dependencies with uv"
	@echo "make test     Run unit tests"
	@echo "make dev      Run the API with reload on port 8000"
	@echo "make init     Create the two AWS indexes"
	@echo "make seed     Seed data; set SEED_RESET=false to preserve indexes"
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
	uv run --locked python -m semantic_search.cli seed "$(SEED_FILE)" \
		--reset "$(SEED_RESET)" $(SEED_START_ARG)

reset: setup
	uv run --locked python -m semantic_search.cli reset
