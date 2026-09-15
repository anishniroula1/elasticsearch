SHELL := /bin/sh

-include .env
export

.PHONY: help setup lock test dev

help:
	@echo "make setup    Install Python dependencies with uv"
	@echo "make test     Run unit tests"
	@echo "make dev      Run the API with reload on port 8000"

setup:
	uv sync

lock:
	uv lock

test: setup
	uv run pytest -q

dev: setup
	uv run --locked uvicorn semantic_search.main:app --reload \
		--host 0.0.0.0 --port 8000
