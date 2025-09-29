.EXPORT_ALL_VARIABLES:

SHELL:=/bin/bash

mkfile_path := $(abspath $(lastword $(MAKEFILE_LIST)))
mkfile_dir := $(shell dirname $(mkfile_path) )
PROJECTNAME := scryfallos

GIT_ROOT := $(shell git rev-parse --show-toplevel)
MAYBENORUN := $(shell if echo | xargs --no-run-if-empty >/dev/null 2>/dev/null; then echo "--no-run-if-empty"; else echo ""; fi)
BASE_COMPOSE := $(mkfile_dir)/docker-compose.yml
PG_DUMP := $(shell find /opt/homebrew -name pg_dump)
LINTABLE_DIRS := .

DOCKER_POSTGRES_HOST=postgres
XPGDATABASE=magic
XPGPASSWORD=foopassword
XPGUSER=foouser

.PHONY: \
	/tmp/PIP_ACCESS_TOKEN \
	/tmp/PIP_INDEX_URL \
	/tmp/auth.toml \
	/tmp/pip.conf \
	build_images \
	check_env \
	coverage \
	dockerclean \
	down \
	ensure_black \
	ensure_isort \
	ensure_pylint \
	help \
	hlep \
	images \
	lint \
	pull_images \
	reset \
	test \
	test-integration \
	test-unit \
	up

help: # @doc show this help and exit
	@python ./scripts/show_makefile_help.py $(mkfile_path)

hlep: help


###  Entry points

up: datadir images down check_env # @doc start services for scryfallos
	cd $(GIT_ROOT) && docker compose --file $(BASE_COMPOSE) up --remove-orphans --abort-on-container-exit

up-detach: datadir images down check_env
	cd $(GIT_ROOT) && docker compose --file $(BASE_COMPOSE) up --remove-orphans --detach

down: # @doc stop all services
	docker compose --file $(BASE_COMPOSE) down --remove-orphans > /dev/null

images: build_images pull_images # @doc refresh images

build_images: # @doc refresh locally built images
	cd $(GIT_ROOT) && \
	docker compose --progress=plain --file $(BASE_COMPOSE) build

pull_images: $(BASE_COMPOSE) # @doc pull images from remote repos
	true || docker compose --file $(BASE_COMPOSE) pull

ensure_black: ensure_uv
	@python -m black --version > /dev/null || \
	python -m uv pip install black

ensure_isort: ensure_uv
	@python -m isort --version > /dev/null || \
	python -m uv pip install isort

ensure_pylint: ensure_uv
	@python -m pylint /dev/null || \
	python -m uv pip install pylint

ensure_pydocker: ensure_uv
	@python -c "import docker" 2>/dev/null || \
	python -m uv pip install docker

ensure_ruff: ensure_uv
	@python -m ruff --version > /dev/null || \
	python -m uv pip install ruff

ensure_uv:
	@python -m uv --version > /dev/null || \
	python -m pip install uv

lint: ensure_ruff ensure_pylint # @doc lint all python files
	find . -type f -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes >/dev/null 2>/dev/null || true
	find . -type f -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes
	find . -type f -name "*.py" | xargs python -m pylint --fail-under 7.0 --max-line-length=132
	npx prettier --write api/index.html

check_env: ensure_pydocker
	true

dockerclean:
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker stop
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker rm --force
	docker images --format '{{.ID}}' | xargs $(MAYBENORUN) docker rmi --force

dbconn: # @doc connect to the local database
	@PGDATABASE=$(XPGDATABASE) \
	PGHOST=127.0.0.1 \
	PGPASSWORD=$(XPGPASSWORD) \
	PGPORT=15432 \
	PGUSER=$(XPGUSER) \
	psql

dump_schema: # @doc dump database schema to file using container's pg_dump
	docker exec scryfallpostgres pg_dump -U $(XPGUSER) -d $(XPGDATABASE) -s

datadir:
	mkdir -p data/api data/postgres /tmp/pgdata

reset:
	rm -rvf data

test:
	python -m pytest -vvv --capture=no --durations=10

test-integration:
	python -m pytest api/tests/test_integration_testcontainers.py -vvv --exitfirst

test-unit:
	python -m pytest -vvv --exitfirst --ignore=api/tests/test_integration_testcontainers.py

coverage: # @doc generate HTML coverage report
	python -m pytest --cov=. --cov-report=html --cov-report=term-missing --durations=10 -vvv

test-profiling:
	python -m pytest --profile-svg --durations=10 -vvv -k TestImportCardByName

