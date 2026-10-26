.PHONY: test lint check

test:
	python -m pytest

lint:
	python -m ruff check src tests

check: lint test
