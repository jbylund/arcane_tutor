.EXPORT_ALL_VARIABLES:

SHELL:=/bin/bash

mkfile_path := $(abspath $(lastword $(MAKEFILE_LIST)))
mkfile_dir := $(shell dirname $(mkfile_path) )
PROJECTNAME := scryfallos

GIT_ROOT := $(shell git rev-parse --show-toplevel)
MAYBENORUN := $(shell if echo | xargs --no-run-if-empty >/dev/null 2>/dev/null; then echo "--no-run-if-empty"; else echo ""; fi)
BASE_COMPOSE := $(mkfile_dir)/docker-compose.yml
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
	up

help: # @doc show this help and exit
	@python ./scripts/show_makefile_help.py $(mkfile_path)

hlep: help


###  Entry points

up: datadir images down check_env # @doc start services for scryfallos
	cd $(GIT_ROOT) && docker compose --file $(BASE_COMPOSE) up --remove-orphans --abort-on-container-exit

down: # @doc stop all services
	docker compose --file $(BASE_COMPOSE) down --remove-orphans > /dev/null

images: build_images pull_images # @doc refresh images

build_images: # @doc refresh locally built images
	cd $(GIT_ROOT) && \
	docker compose --progress=plain --file $(BASE_COMPOSE) build

pull_images: $(BASE_COMPOSE) # @doc pull images from remote repos
	true || docker compose --file $(BASE_COMPOSE) pull

ensure_black:
	@python -m black --version > /dev/null || uv pip install --system --break-system-packages black

ensure_isort:
	@python -m isort --version > /dev/null || uv pip install --system --break-system-packages isort

ensure_pylint:
	@python -m pylint /dev/null || uv pip install --system --break-system-packages pylint

ensure_pydocker:
	@python -c "import docker" 2>/dev/null || uv pip install --system --break-system-packages docker

ensure_ruff:
	@python -m ruff --version > /dev/null || uv pip install --system --break-system-packages ruff

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

datadir:
	mkdir -p data/api data/postgres /tmp/pgdata

reset:
	rm -rvf data

test:
	python -m pytest -vvv --exitfirst
