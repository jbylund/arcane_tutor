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
XPGPORT=15432
XPGUSER=foouser

.PHONY: \
	/tmp/PIP_ACCESS_TOKEN \
	/tmp/PIP_INDEX_URL \
	/tmp/auth.toml \
	/tmp/pip.conf \
	beleren_font \
	build_images \
	check_env \
	coverage \
	dockerclean \
	down \
	ensure_black \
	ensure_isort \
	ensure_pylint \
	fonts \
	help \
	hlep \
	images \
	lint \
	mplantin_font \
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

dbconn: # @doc connect to the local database
	@PGDATABASE=$(XPGDATABASE) \
	PGHOST=127.0.0.1 \
	PGPASSWORD=$(XPGPASSWORD) \
	PGPORT=15432 \
	PGUSER=$(XPGUSER) \
	psql

dump_schema: # @doc dump database schema to file using container's pg_dump
	docker exec scryfallpostgres $(shell find /usr/bin /opt/homebrew -name pg_dump) -U $(XPGUSER) -d $(XPGDATABASE) -s

datadir:
	mkdir -p data/api data/postgres data/postgres/logs /tmp/pgdata

reset:
	rm -rvf data

install_deps:
	python -m uv pip install -r requirements.txt

install_test_deps:
	python -m uv pip install -r test-requirements.txt -r requirements.txt

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

fonts: # @doc subset and optimize the Mana font for web delivery
	@echo "Installing font subsetting dependencies..."
	@python -m uv pip install fonttools brotli requests boto3 2>/dev/null || python -m pip install fonttools brotli requests boto3
	@echo "Running font subsetting script..."
	@if [ -z "$(S3_BUCKET)" ]; then \
		echo "Note: S3_BUCKET not set. Generating fonts locally only."; \
		echo "To auto-upload, run: make fonts S3_BUCKET=your-bucket-name"; \
		python scripts/subset_mana_font.py --output-dir data/fonts/mana --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mana --skip-upload; \
	else \
		echo "Uploading to S3 bucket: $(S3_BUCKET)"; \
		python scripts/subset_mana_font.py --output-dir data/fonts/mana --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mana --s3-bucket $(S3_BUCKET) --s3-prefix cdn/fonts/mana; \
	fi
	@echo ""
	@echo "See docs/font_optimization.md for next steps"

beleren_font: # @doc subset and optimize the Beleren font for web delivery
	@echo "Installing font subsetting dependencies..."
	@python -m uv pip install fonttools brotli requests boto3 2>/dev/null || python -m pip install fonttools brotli requests boto3
	@echo "Running Beleren font subsetting script..."
	@if [ -z "$(S3_BUCKET)" ]; then \
		echo "Note: S3_BUCKET not set. Generating fonts locally only."; \
		echo "To auto-upload, run: make beleren_font S3_BUCKET=your-bucket-name"; \
		python scripts/subset_beleren_font.py --output-dir data/fonts/beleren --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/beleren --skip-upload; \
	else \
		echo "Uploading to S3 bucket: $(S3_BUCKET)"; \
		python scripts/subset_beleren_font.py --output-dir data/fonts/beleren --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/beleren --s3-bucket $(S3_BUCKET) --s3-prefix cdn/fonts/beleren; \
	fi
	@echo ""
	@echo "✓ Beleren font subsetting complete!"

mplantin_font: # @doc subset and optimize the MPlantin font for web delivery
	@echo "Installing font subsetting dependencies..."
	@python -m uv pip install fonttools brotli boto3 2>/dev/null || python -m pip install fonttools brotli boto3
	@echo "Running MPlantin font subsetting script..."
	@if [ -z "$(S3_BUCKET)" ]; then \
		echo "Note: S3_BUCKET not set. Generating fonts locally only."; \
		echo "To auto-upload, run: make mplantin_font S3_BUCKET=your-bucket-name"; \
		python scripts/subset_mplantin_font.py --input-font fonts/mplantin.otf --output-dir data/fonts/mplantin --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mplantin --skip-upload; \
	else \
		echo "Uploading to S3 bucket: $(S3_BUCKET)"; \
		python scripts/subset_mplantin_font.py --input-font fonts/mplantin.otf --output-dir data/fonts/mplantin --cdn-url https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mplantin --s3-bucket $(S3_BUCKET) --s3-prefix cdn/fonts/mplantin; \
	fi
	@echo ""
	@echo "✓ MPlantin font subsetting complete!"
