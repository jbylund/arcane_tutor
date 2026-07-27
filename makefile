ifndef MAKEFLAGS
CPUS ?= $(shell nproc)
MAKEFLAGS += -j $(CPUS) -l $(CPUS) -s
$(info Note: running on $(CPUS) CPU cores by default, use flag -j to override.)
endif

.EXPORT_ALL_VARIABLES:

SHELL:=/bin/bash

mkfile_path := $(abspath $(lastword $(MAKEFILE_LIST)))
mkfile_dir := $(shell dirname $(mkfile_path) )
PROJECTNAME := sylvan_librarian

GIT_ROOT := $(shell git rev-parse --show-toplevel)
GIT_SHA := $(shell git rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH := $(shell git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
MAYBENORUN := $(shell if echo | xargs --no-run-if-empty >/dev/null 2>/dev/null; then echo "--no-run-if-empty"; else echo ""; fi)
BASE_COMPOSE := $(mkfile_dir)/docker-compose.yml
ENVS := $(shell ls envs)
LINTABLE_DIRS := .

# Local credentials come from env.json, created here on first run so that a fresh
# clone has everything it needs to boot. Read them back into make variables (which
# .EXPORT_ALL_VARIABLES puts in every recipe's environment) so the values compose
# sees match .env exactly — compose gives the shell environment precedence over
# --env-file, so any disagreement means env.json is silently ignored.
$(shell $(GIT_ROOT)/scripts/gen_env_json.sh $(GIT_ROOT)/env.json)

XPGDATABASE := $(shell jq -r '.XPGDATABASE' $(GIT_ROOT)/env.json)
XPGPASSWORD := $(shell jq -r '.XPGPASSWORD' $(GIT_ROOT)/env.json)
XPGUSER := $(shell jq -r '.XPGUSER' $(GIT_ROOT)/env.json)
HOSTNAME := $(shell hostname)

S3_BUCKET=biblioplex

html_files := $(shell git ls-files "*.html")
js_files := $(shell git ls-files "*.js")

requirements_sources := $(shell find requirements -type f -name "*.txt")
PYTHON_DIRS := $(shell git ls-files "*.py" | cut -f 1 -d/ | sort -u)
python_sources := $(shell find api client -type f -name "*.py")
engine_sources := $(shell find card_engine/src -type f -name "*.rs") card_engine/Cargo.toml card_engine/Cargo.lock card_engine/pyproject.toml
ENGINE_EXT_SUFFIX := $(shell python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
ENGINE_SO := card_engine/card_engine/card_engine$(ENGINE_EXT_SUFFIX)
image_sources := $(python_sources) api/Dockerfile client/Dockerfile $(requirements_sources) $(BASE_COMPOSE)

BUILD_STAMP_DIR := $(GIT_ROOT)/.tmp/build-stamps
BUILD_HASH := $(shell { git rev-parse HEAD 2>/dev/null; git diff origin/main 2>/dev/null; } | md5sum | cut -d' ' -f1)
BUILD_STAMP := $(BUILD_STAMP_DIR)/$(BUILD_HASH).stamp
IMAGE_TAG := $(BUILD_HASH)

.PHONY: \
	beleren_font \
	build_images \
	check_env \
	compare-minification \
	coverage \
	dockerclean \
	down \
	engine \
	fonts \
	help \
	hlep \
	images \
	js-deps \
	lint \
	mplantin_font \
	postgres-config \
	psql-dotfiles \
	pull_images \
	reset \
	rolling-deploy \
	status \
	test \
	test-integration \
	test-unit

postgres-config: configs/postgres/conf/postgresql.conf # @doc generate postgresql.conf from template scaled to available memory

configs/postgres/conf/postgresql.conf: configs/postgres/conf/postgresql.conf.template scripts/gen_postgres_conf.py
	python scripts/gen_postgres_conf.py \
		--template $< \
		--output $@ \
		--env-output envs

help: # @doc show this help and exit
	@python ./scripts/show_makefile_help.py $(mkfile_path)

hlep: help


###  Entry points

up_deps: images check_env .env api/static/app.min.js configs/postgres/conf/postgresql.conf

deps-%: up_deps psql-dotfiles
	mkdir -p $(GIT_ROOT)/data/api/$* && chmod 755 $(GIT_ROOT)/data/api/$*

# Both files are bind-mounted into the postgres container. Docker silently creates
# a directory in place of a missing bind-mount source, so on a host that has never
# run psql they turn into directories rather than files.
psql-dotfiles:
	@touch ~/.psqlrc ~/.psql_history

env.json: # @doc create env.json with generated local credentials if missing (never overwrite)
	@$(GIT_ROOT)/scripts/gen_env_json.sh $@

.env: env.json
	cat env.json | jq -r 'to_entries[] | "\(.key)=\(.value)"' | sort > $@

%-up: deps-% # @doc start an environment in the foreground, e.g. make dev-up
	cd $(GIT_ROOT) && docker compose --project-name sylvan_$* --env-file .env --env-file envs/$* --file $(BASE_COMPOSE) up --remove-orphans --abort-on-container-exit

%-up-detach: deps-% # @doc start an environment in the background, e.g. make dev-up-detach
	cd $(GIT_ROOT) && docker compose --project-name sylvan_$* --env-file .env --env-file envs/$* --file $(BASE_COMPOSE) up --remove-orphans --detach

%-down: | .env # @doc stop an environment, e.g. make dev-down
	cd $(GIT_ROOT) && docker compose --project-name sylvan_$* --env-file .env --env-file envs/$* --file $(BASE_COMPOSE) down --remove-orphans

status: | .env # @doc show container status for all environments
	@$(foreach env,$(ENVS), \
	  python -c "import shutil; w=shutil.get_terminal_size().columns; print(' $(env) '.center(w, '='))" && \
	  cd $(GIT_ROOT) && docker compose --project-name sylvan_$(env) --env-file .env --env-file envs/$(env) --file $(BASE_COMPOSE) ps --all ; \
	)

rolling-deploy: deps-blue deps-green # @doc rolling blue/green deploy — update blue (wait for healthy), then green
	@echo "=== Deploying blue ==="
	cd $(GIT_ROOT) && docker compose --project-name sylvan_blue --env-file .env --env-file envs/blue --file $(BASE_COMPOSE) up --remove-orphans --detach --wait
	@echo "=== Blue healthy. Deploying green ==="
	cd $(GIT_ROOT) && docker compose --project-name sylvan_green --env-file .env --env-file envs/green --file $(BASE_COMPOSE) up --remove-orphans --detach --wait
	@echo "=== Rolling deploy complete ==="

down: $(addsuffix -down,$(ENVS)) # @doc stop every environment

images: build_images pull_images # @doc refresh images

build_images: $(BUILD_STAMP) # @doc refresh locally built images

$(BUILD_STAMP): $(image_sources) | .env
	mkdir -p $(BUILD_STAMP_DIR)
	find $(BUILD_STAMP_DIR) -name "*.stamp" -mtime +3 -delete 2>/dev/null || true
	cd $(GIT_ROOT) && docker compose --progress=plain --env-file .env --env-file envs/dev --file $(BASE_COMPOSE) build
	touch $@

pull_images: $(BASE_COMPOSE) | .env # @doc pull images from remote repos
	true || docker compose --env-file .env --env-file envs/dev --file $(BASE_COMPOSE) pull

ensure_pydocker: ensure_uv
	@python -c "import docker" 2>/dev/null || \
	python -m uv pip install docker

ensure_ruff: ensure_uv
	@python -m ruff --version > /dev/null || \
	python -m uv pip install ruff

ensure_uv:
	@python -m uv --version > /dev/null || \
	python -m pip install uv || \
	uv pip install --python "$$(command -v python)" uv

lint: ruff_lint prettier_lint # @doc lint all python files
	true

prettier_lint: /tmp/prettier.stamp
	true

/tmp/prettier.stamp: $(html_files) $(js_files)
	npx prettier --write $(html_files) $(js_files)
	touch /tmp/prettier.stamp

ruff_fix: ensure_ruff
	find $(PYTHON_DIRS) -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes >/dev/null 2>/dev/null || true
	find $(PYTHON_DIRS) -name "*.py" | xargs python -m ruff format

ruff_lint: ruff_fix
	find $(PYTHON_DIRS) -name "*.py" | xargs python -m ruff check --fix --unsafe-fixes

check_env: ensure_pydocker
	true

dockerclean:
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker stop
	docker ps --all --format '{{.ID}}' | xargs $(MAYBENORUN) docker rm --force
	docker images --format '{{.ID}}' | xargs $(MAYBENORUN) docker rmi --force

dbconn-%: psql-dotfiles | .env # @doc open psql against an environment, e.g. make dbconn-blue
	cd $(GIT_ROOT) && docker compose --project-name sylvan_$* --env-file .env --env-file envs/$* --file $(BASE_COMPOSE) \
	  exec -e PSQLRC=/var/lib/postgresql/.psqlrc -e PSQL_HISTORY=/var/lib/postgresql/.psql_history \
	  postgres psql -U $(XPGUSER) -d $(XPGDATABASE) --host=localhost

reset-%: | .env # @doc destroy an environment including its database volume
	docker compose --project-name sylvan_$* --env-file .env --env-file envs/$* --file $(BASE_COMPOSE) down --volumes --remove-orphans
	rm -rvf data/api/$* data/postgres/$*

reset: $(addprefix reset-,$(ENVS)) # @doc destroy every environment including databases

install_deps:
	python -m uv pip install -r requirements/base.txt

install_test_deps:
	python -m uv pip install -r requirements/test.txt -r requirements/base.txt

js-deps: # @doc update JavaScript dependencies and apply safe security fixes
	npm update
	npm audit fix || true

engine: $(ENGINE_SO) # @doc build the Rust card engine extension if its sources changed

$(ENGINE_SO): $(engine_sources)
	@maturin --version > /dev/null 2>&1 || python -m uv pip install maturin
	cd card_engine && PATH="$$HOME/.cargo/bin:$$PATH" maturin develop --release

test tests: install_test_deps engine
	python -m pytest -vvv --capture=no --durations=10

test-integration: engine
	python -m pytest api/tests/test_integration_testcontainers.py -vvv --exitfirst

test-unit: engine
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

compare-minification: # @doc compare file sizes: uncompressed, compressed, minified, and minified+compressed
	@echo "Installing minifier dependencies..."
	@npm install --no-save cssnano postcss postcss-cli terser > /dev/null 2>&1 || true
	@python scripts/compare_minification.py

api/static/app.min.js: api/static/app.js # @doc minify app.js (used in both dev and prod)
	@echo "Minifying $^..."
	@npm install --no-save terser > /dev/null 2>&1 || true
	@npx terser api/static/app.js --compress --mangle --output $@
	@echo "Created $@"
