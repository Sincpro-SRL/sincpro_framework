GEMFURY_PUSH_TOKEN ?= DEFAULT_TOKEN
POETRY_PYPI_TOKEN ?= DEFAULT_TOKEN

.SILENT: configure-gemfury

add-gemfury-repo:
	poetry config repositories.fury https://pypi.fury.io/sincpro/

configure-gemfury: add-gemfury-repo
	poetry config http-basic.fury $(GEMFURY_PUSH_TOKEN) NOPASS

prepare-environment:
	echo "Using pipx, if not installed, please install it first: https://pypa.github.io/pipx/installation/"
	pipx install poetry
	pipx install black
	pipx install autoflake
	pipx install isort
	pipx install pyright
	pipx install pre-commit
	pipx ensurepath
	pre-commit install

install: add-gemfury-repo
	poetry install

init: prepare-environment install

ipython:
	poetry run ipython

format-yaml:
	@if command -v prettier > /dev/null; then \
		echo "Formatting YAML files with prettier..."; \
		prettier --write "**/*.yml" "**/*.yaml"; \
	else \
		echo "prettier not found. Install with: npm install -g prettier"; \
	fi
	@if command -v yamllint > /dev/null; then \
		echo "Linting YAML files with yamllint..."; \
		yamllint -f parsable sincpro_framework/conf/*.yml tests/config/resources/*.yml; \
	else \
		echo "yamllint not found. Install with: pip install yamllint"; \
	fi

format-python:
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports  -r sincpro_framework
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports  -r tests
	poetry run isort sincpro_framework
	poetry run isort tests
	poetry run black sincpro_framework
	poetry run black tests
	make format-yaml

format: format-python format-yaml

verify-format: format
	@if ! git diff --quiet; then \
	  echo >&2 "✘ El formateo ha modificado archivos. Por favor agrégalos al commit."; \
	  git --no-pager diff --name-only HEAD -- >&2; \
	  exit 1; \
	fi


clean-pyc:
	find . -type d -name '__pycache__' -exec rm -rf {} \; || exit 0
	find . -type f -iname '*.pyc' -delete || exit 0

build: configure-gemfury
	poetry build

publish: configure-gemfury
	poetry publish -r fury --build
	poetry publish -u __token__ -p $(POETRY_PYPI_TOKEN)

test:
	poetry run pytest tests

test_debug:
	poetry run pytest -vvs tests

test_one:
	poetry run pytest ${t} -vvs

.PHONY: install start clean test build format format-yaml format-all