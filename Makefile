install:
	poetry install

ipython:
	poetry run ipython

format:
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports -r sincpro_framework
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports -r tests
	poetry run isort sincpro_framework
	poetry run isort tests
	poetry run black sincpro_framework
	poetry run black tests

clean-pyc:
	find . -type d -name '__pycache__' -exec rm -rf {} \; || exit 0
	find . -type f -iname '*.pyc' -delete || exit 0

build:
	poetry build

test:
	poetry run pytest tests

test_debug:
	poetry run pytest -vvs tests

test_one:
	poetry run pytest ${t} -vvs

.PHONY: install start clean test build