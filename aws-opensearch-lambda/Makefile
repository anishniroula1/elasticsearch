.PHONY: setup test build validate clean

setup:
	uv sync

test: setup
	uv run pytest -q

build:
	sam build

validate:
	sam validate --lint

clean:
	rm -rf .aws-sam .pytest_cache .coverage htmlcov
