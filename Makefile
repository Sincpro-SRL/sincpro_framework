GEMFURY_PUSH_TOKEN ?= DEFAULT_TOKEN

.SILENT: configure-gemfury

add-gemfury-repo:
	poetry config repositories.fury https://pypi.fury.io/sincpro/

configure-gemfury: add-gemfury-repo
	poetry config http-basic.fury $(GEMFURY_PUSH_TOKEN) NOPASS


install: add-gemfury-repo
	poetry install

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

format:
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports  -r sincpro_framework
	poetry run autoflake --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports  -r tests
	poetry run isort sincpro_framework
	poetry run isort tests
	poetry run black sincpro_framework
	poetry run black tests
	make format-yaml

format-all: format format-yaml

clean-pyc:
	find . -type d -name '__pycache__' -exec rm -rf {} \; || exit 0
	find . -type f -iname '*.pyc' -delete || exit 0

build: configure-gemfury
	poetry build

publish: configure-gemfury
	poetry publish -r fury --build

test:
	poetry run pytest tests

test_debug:
	poetry run pytest -vvs tests

test_one:
	poetry run pytest ${t} -vvs

.PHONY: install start clean test build format format-yaml format-all