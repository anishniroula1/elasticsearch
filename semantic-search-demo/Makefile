SHELL := /bin/sh

-include .env
export

SEED_FILE ?= data/seed.csv

.PHONY: help setup lock test init seed reset

help:
	@echo "make setup    Install Python dependencies with uv"
	@echo "make test     Run unit tests"
	@echo "make init     Create the two AWS indexes"
	@echo "make seed     Recreate indexes and embed unique semantic text"
	@echo "make reset    Recreate both indexes without seed data"

setup:
	uv sync

lock:
	uv lock

test: setup
	uv run pytest -q

init: setup
	uv run --locked python -m semantic_search.cli init

seed: setup
	uv run --locked python -m semantic_search.cli seed "$(SEED_FILE)"

reset: setup
	uv run --locked python -m semantic_search.cli reset
