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
# The ONLY secret. Read from the environment, or from ~/.openwiki/.env when the
# shell does not define it. NEVER assign a value here and never commit one.
# It is exported only when non-empty: exporting it empty would shadow the value
# stored in ~/.openwiki/.env and openwiki would report a missing key.
OPENAI_COMPATIBLE_API_KEY ?=
ifneq ($(OPENAI_COMPATIBLE_API_KEY),)
export OPENAI_COMPATIBLE_API_KEY
endif

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

# openwiki 0.5.1 does not create a page's parent directory before writing it,
# and its rollback then dies with ENOENT, killing the whole run and losing the
# page queue. Pre-create every directory the current plan needs. No-op on a
# first run, when no plan exists yet.
docs-dirs:
	@test -f openwiki/.run.json || exit 0; \
	python3 -c "import json,os; \
d=json.load(open('openwiki/.run.json')); \
ps=(d.get('plan') or {}).get('pages') or []; \
ds={os.path.dirname(p.get('path','').lstrip('/')) for p in ps}; \
ds={x for x in ds if x}; \
[os.makedirs(x, exist_ok=True) for x in ds]; \
[os.makedirs(x.replace('openwiki/','openwiki/.claims/',1), exist_ok=True) for x in ds]; \
print('directorios del plan asegurados:', len(ds))"

# Single entry point. Picks the mode on its own and never asks anything:
#   - interrupted run  -> resume in the same mode it was started in
#   - existing wiki    -> --update (only pages whose sources changed)
#   - nothing yet      -> --init
# Always non-interactive (--print plus stdin from /dev/null), and the CI workflow
# openwiki insists on generating is deleted afterwards, success or failure.
# Follow progress from another terminal with `make docs-status`.
docs: check-openwiki docs-dirs
	@mode=--init; \
	if [ -f openwiki/.run.json ]; then \
	  m=$$(python3 -c "import json;print(json.load(open('openwiki/.run.json')).get('mode',''))" 2>/dev/null); \
	  if [ "$$m" = "update" ]; then mode=--update; fi; \
	  echo "-> corrida interrumpida (modo $$m): retomando con $$mode"; \
	elif [ -f openwiki/.page-manifest.json ]; then \
	  mode=--update; echo "-> wiki existente: actualizando"; \
	else \
	  echo "-> sin wiki previa: generando desde cero"; \
	fi; \
	$(OPENWIKI) $$mode --print </dev/null; \
	status=$$?; \
	rm -f .github/workflows/openwiki-update.yml; \
	exit $$status

# Escapes: force one mode when you know better than the detection above.
docs-init: check-openwiki docs-dirs
	@$(OPENWIKI) --init --print </dev/null; \
	status=$$?; rm -f .github/workflows/openwiki-update.yml; exit $$status

docs-update: check-openwiki docs-dirs
	@$(OPENWIKI) --update --print </dev/null; \
	status=$$?; rm -f .github/workflows/openwiki-update.yml; exit $$status

docs-view: check-openwiki
	$(OPENWIKI) visualize openwiki

# Progress of a running (or finished) wiki generation. Read-only, no API calls.
docs-status:
	@echo "paginas escritas: $$(find openwiki -name '*.md' ! -name INSTRUCTIONS.md 2>/dev/null | wc -l | tr -d ' ')"
	@if pgrep -f "openwiki@" >/dev/null 2>&1; then echo "openwiki activo: si (en alguna carpeta de esta maquina)"; else echo "openwiki activo: no"; fi
	@if [ -f openwiki/.run.json ]; then \
	  python3 -c "import json,datetime; d=json.load(open('openwiki/.run.json')); p=(d.get('plan') or {}).get('pages') or []; s=datetime.datetime.fromisoformat(d['startedAt'].replace('Z','+00:00')); print('fase:            '+str(d.get('phase'))+'  (idioma '+str(d.get('language'))+')'); print('plan:            '+str(len(p))+' paginas'); print('transcurrido:    '+str(datetime.datetime.now(datetime.timezone.utc)-s).split('.')[0])"; \
	else echo "fase:            sin corrida activa (sin .run.json)"; fi
	@last=$$(ls -t openwiki/**/*.md openwiki/*.md 2>/dev/null | grep -v INSTRUCTIONS | head -1); \
	if [ -n "$$last" ]; then echo "ultima pagina:   $$last (hace $$(( ($$(date +%s) - $$(stat -f %m "$$last")) / 60 )) min)"; fi

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
	docs docs-init docs-update docs-view docs-dirs docs-status check-openwiki
