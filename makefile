.EXPORT_ALL_VARIABLES:

SHELL:=/bin/bash

mkfile_path := $(abspath $(lastword $(MAKEFILE_LIST)))
mkfile_dir := $(shell dirname $(mkfile_path) )
PROJECTNAME := arcane_tutor

GIT_ROOT := $(shell git rev-parse --show-toplevel)
MAYBENORUN := $(shell if echo | xargs --no-run-if-empty >/dev/null 2>/dev/null; then echo "--no-run-if-empty"; else echo ""; fi)
BASE_COMPOSE := $(mkfile_dir)/docker-compose.yml
DEV_COMPOSE := $(mkfile_dir)/docker-compose.dev.yml
PROD_COMPOSE := $(mkfile_dir)/docker-compose.prod.yml
LINTABLE_DIRS := .

XPGDATABASE=magic
XPGPASSWORD=foopassword
XPGPORT=15432
XPGPORT_DEV=25432
XPGUSER=foouser

S3_BUCKET=biblioplex

.PHONY: \
	/tmp/PIP_ACCESS_TOKEN \
	/tmp/PIP_INDEX_URL \
	/tmp/auth.toml \
	/tmp/pip.conf \
	beleren_font \
	build_images \
	build_images_dev \
	build_images_prod \
	check_env \
	coverage \
	dockerclean \
	down \
	down-dev \
	down-prod \
	ensure_black \
	ensure_isort \
	ensure_pylint \
	fonts \
	help \
	hlep \
	images \
	images_dev \
	images_prod \
	lint \
	mplantin_font \
	pull_images \
	pull_images_dev \
	pull_images_prod \
	reset \
	test \
	test-integration \
	test-unit \
	up \
	up-dev \
	up-prod \
	up-detach \
	up-detach-dev \
	up-detach-prod

help: # @doc show this help and exit
	@python ./scripts/show_makefile_help.py $(mkfile_path)

hlep: help


###  Entry points

up: up-dev # @doc start services in development mode (default)

up-dev: datadir images_dev down-dev check_env # @doc start services in development mode
	cd $(GIT_ROOT) && docker compose --file $(DEV_COMPOSE) up --remove-orphans --abort-on-container-exit

up-prod: datadir images_prod down-prod check_env # @doc start services in production mode
	cd $(GIT_ROOT) && docker compose --file $(PROD_COMPOSE) up --remove-orphans --abort-on-container-exit

up-detach: up-detach-dev # @doc start services in development mode (detached, default)

up-detach-dev: datadir images_dev down-dev check_env # @doc start services in development mode (detached)
	cd $(GIT_ROOT) && docker compose --file $(DEV_COMPOSE) up --remove-orphans --detach

up-detach-prod: datadir images_prod down-prod check_env # @doc start services in production mode (detached)
	cd $(GIT_ROOT) && docker compose --file $(PROD_COMPOSE) up --remove-orphans --detach

down: down-dev # @doc stop all services (development mode default)

down-dev: # @doc stop development services
	docker compose --file $(DEV_COMPOSE) down --remove-orphans > /dev/null

down-prod: # @doc stop production services
	docker compose --file $(PROD_COMPOSE) down --remove-orphans > /dev/null

images: build_images pull_images # @doc refresh images (uses base compose)

images_dev: build_images_dev pull_images_dev # @doc refresh images for development

images_prod: build_images_prod pull_images_prod # @doc refresh images for production

build_images: # @doc refresh locally built images (uses base compose)
	cd $(GIT_ROOT) && \
	docker compose --progress=plain --file $(BASE_COMPOSE) build

build_images_dev: # @doc refresh locally built images for development
	cd $(GIT_ROOT) && \
	docker compose --progress=plain --file $(DEV_COMPOSE) build

build_images_prod: # @doc refresh locally built images for production
	cd $(GIT_ROOT) && \
	docker compose --progress=plain --file $(PROD_COMPOSE) build

pull_images: $(BASE_COMPOSE) # @doc pull images from remote repos (uses base compose)
	true || docker compose --file $(BASE_COMPOSE) pull

pull_images_dev: $(DEV_COMPOSE) # @doc pull images from remote repos for development
	true || docker compose --file $(DEV_COMPOSE) pull

pull_images_prod: $(PROD_COMPOSE) # @doc pull images from remote repos for production
	true || docker compose --file $(PROD_COMPOSE) pull

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

lint: ruff_lint prettier_lint # @doc lint all python files
	true

prettier_lint: /tmp/prettier.stamp
	true

/tmp/prettier.stamp: api/index.html
	npx prettier --write api/index.html
	touch /tmp/prettier.stamp

ruff_fix: ensure_ruff
	find . -type f -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes >/dev/null 2>/dev/null || true

ruff_lint: ruff_fix
	find . -type f -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes

# pylint_lint: ruff_fix ensure_pylint
# 	find . -type f -name "*.py" | xargs python -m pylint --fail-under 7.0 --max-line-length=132

check_env: ensure_pydocker
	true

dockerclean:
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker stop
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker rm --force
	docker images --format '{{.ID}}' | xargs $(MAYBENORUN) docker rmi --force

dbconn: # @doc connect to the local database (production)
	@PGDATABASE=$(XPGDATABASE) \
	PGHOST=127.0.0.1 \
	PGPASSWORD=$(XPGPASSWORD) \
	PGPORT=$(XPGPORT) \
	PGUSER=$(XPGUSER) \
	psql

dbconn-dev: # @doc connect to the local database (development)
	@PGDATABASE=$(XPGDATABASE) \
	PGHOST=127.0.0.1 \
	PGPASSWORD=$(XPGPASSWORD) \
	PGPORT=$(XPGPORT_DEV) \
	PGUSER=$(XPGUSER) \
	psql

dump_schema: # @doc dump database schema to file using container's pg_dump
	docker exec $(PROJECTNAME)postgres $(shell find /usr/bin /opt/homebrew -name pg_dump) -U $(XPGUSER) -d $(XPGDATABASE) -s

datadir:
	mkdir -p data/api data/postgres data/postgres/logs /tmp/pgdata

reset:
	rm -rvf data

install_deps:
	python -m uv pip install -r requirements/base.txt

install_test_deps:
	python -m uv pip install -r requirements/test.txt -r requirements/base.txt

test tests: install_test_deps
	python -m pytest -vvv --capture=no --durations=10

test-integration:
	python -m pytest api/tests/test_integration_testcontainers.py -vvv --exitfirst

test-unit:
	python -m pytest -vvv --exitfirst --ignore=api/tests/test_integration_testcontainers.py

coverage: # @doc generate HTML coverage report
	python -m pytest --cov=. --cov-report=html --cov-report=term-missing --durations=10 -vvv

test-profiling:
	python -m pytest --profile-svg --durations=10 -vvv -k TestImportCardByName

font-dependencies:
	echo "Installing font subsetting dependencies..."
	python -m uv pip install -r requirements/fonts.txt

fonts: mana_font beleren_font mplantin_font

mana_font: font-dependencies # @doc subset and optimize the Mana font for web delivery
	python scripts/subset_mana_font.py \
		--output-dir data/fonts/mana \
		--cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mana \
		--s3-bucket $(S3_BUCKET) \
		--s3-prefix cdn/fonts/mana

beleren_font: font-dependencies # @doc subset and optimize the Beleren font for web delivery
	python scripts/subset_beleren_font.py \
		--output-dir data/fonts/beleren \
		--cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/beleren \
		--s3-bucket $(S3_BUCKET) \
		--s3-prefix cdn/fonts/beleren

mplantin_font: font-dependencies # @doc subset and optimize the MPlantin font for web delivery
	python scripts/subset_mplantin_font.py \
		--input-font fonts/mplantin.otf \
		--output-dir data/fonts/mplantin \
		--cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mplantin \
		--s3-bucket $(S3_BUCKET) \
		--s3-prefix cdn/fonts/mplantin
