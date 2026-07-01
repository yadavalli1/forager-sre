.PHONY: install test lint format serve docker

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check forager tests
	ruff format --check forager tests

format:
	ruff check --fix forager tests
	ruff format forager tests

serve:
	forager serve

docker:
	docker build -f Dockerfile.agent -t forager-sre .
