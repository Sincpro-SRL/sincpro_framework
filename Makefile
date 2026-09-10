GEMFURY_PUSH_TOKEN ?= DEFAULT_TOKEN
POETRY_PYPI_TOKEN ?= DEFAULT_TOKEN

OPENWIKI_VERSION ?= 0.5.1
OPENWIKI := npx --yes openwiki@$(OPENWIKI_VERSION)

OPENWIKI_PROVIDER          ?= openai-compatible
OPENWIKI_MODEL_ID          ?= deepseek-v4-flash-vision-exp
OPENAI_COMPATIBLE_BASE_URL ?= https://api.deepseek.com/v1

OPENWIKI_TELEMETRY_DISABLED ?= 1
export OPENWIKI_PROVIDER OPENWIKI_MODEL_ID OPENAI_COMPATIBLE_BASE_URL
export OPENWIKI_TELEMETRY_DISABLED
export OPENAI_COMPATIBLE_API_KEY

COVERAGE_MIN ?= 85
COVERAGE_ARGS = --cov --cov-fail-under=$(COVERAGE_MIN)

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

check-openwiki:
	@command -v npx >/dev/null 2>&1 || { \
	  echo >&2 "✘ npx not found. Install Node >= 22."; exit 1; }
	@node -e 'process.exit(parseInt(process.versions.node, 10) >= 22 ? 0 : 1)' || { \
	  echo >&2 "✘ Node >= 22 required, found $$(node -v)."; exit 1; }
	@test -n "$$OPENAI_COMPATIBLE_API_KEY" || test -f "$$HOME/.openwiki/.env" || { \
	  echo >&2 "✘ OPENAI_COMPATIBLE_API_KEY is not set."; \
	  echo >&2 "   Provider, model and endpoint are already set in this Makefile;"; \
	  echo >&2 "   the key is the only thing missing. Either export it:"; \
	  echo >&2 "     export OPENAI_COMPATIBLE_API_KEY=<key>"; \
	  echo >&2 "   or store it in ~/.openwiki/.env (chmod 600)."; \
	  echo >&2 "   Never put it in the Makefile or any versioned file."; \
	  exit 1; }

docs-init: check-openwiki
	$(OPENWIKI) --init

docs: check-openwiki
	$(OPENWIKI) --update

docs-view: check-openwiki
	$(OPENWIKI) visualize openwiki

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

verify-format: format lint
	@if ! git diff --quiet; then \
	  echo >&2 "✘ El formateo ha modificado archivos. Por favor agrégalos al commit."; \
	  git --no-pager diff --name-only HEAD -- >&2; \
	  exit 1; \
	fi
	@echo "✔ Format and lint checks passed."


lint:
	@echo "-------------"
	@echo "Main code"
	@echo "-------------"
	@echo
	@poetry run pyright sincpro_framework 
	@echo
	@echo "-------------"
	@echo "Tests"
	@echo "-------------"
	@poetry run pyright tests


clean-pyc:
	find . -type d -name '__pycache__' -exec rm -rf {} \; || exit 0
	find . -type f -iname '*.pyc' -delete || exit 0

build: configure-gemfury
	poetry build

update-version:
ifndef VERSION
	$(error VERSION is required. Usage: make update-version VERSION=1.2.3)
endif
	@echo "Updating version to $(VERSION) using Poetry..."
	poetry version $(VERSION)
	@echo "Version updated successfully"

publish: configure-gemfury
	poetry publish -r fury --build
	poetry publish -u __token__ -p $(POETRY_PYPI_TOKEN)

test:
	poetry run pytest tests $(COVERAGE_ARGS) --cov-report=term-missing --cov-report=xml

test-coverage: test
	poetry run coverage html
	@echo "HTML coverage report: htmlcov/index.html"

test-coverage-open: test-coverage
	@if command -v open > /dev/null; then \
		open htmlcov/index.html; \
	else \
		xdg-open htmlcov/index.html; \
	fi

test_debug:
	poetry run pytest -vvs tests

test_one:
	poetry run pytest ${t} -vvs

clean-coverage:
	rm -rf htmlcov coverage.xml .coverage .coverage.*

.PHONY: install start clean test test-coverage test-coverage-open clean-coverage build format format-yaml format-all \
	docs docs-init docs-view check-openwiki
