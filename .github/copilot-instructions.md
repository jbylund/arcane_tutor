# Scryfall OS

Scryfall OS is an open source implementation of Scryfall, a Magic: The Gathering card search engine. This project consists of a Python-based API service, a simple HTML/JavaScript web client, and a PostgreSQL database, designed to be deployed via Docker Compose.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

### Prerequisites and Environment Setup
- Install system dependencies:
  - `sudo apt-get update` -- takes ~7 seconds. NEVER CANCEL.
  - `sudo apt-get install -y libev-dev` -- takes ~5 seconds. NEVER CANCEL.
- Install Python dependencies:
  - `python -m pip install --upgrade pip` -- takes ~2 seconds.
  - `python -m pip install -r requirements.txt -r test-requirements.txt` -- takes ~4 seconds.
- **IMPORTANT**: For local API testing, also install: `python -m pip install bjoern` -- takes ~3 seconds. Includes compilation of bjoern C extension.

### Build and Test Workflow
- **Run tests**: `python -m pytest -vvv` -- takes ~1 second. All 100 tests should pass.
- **Run linting**: `python -m ruff check` -- takes <1 second. Should pass with "All checks passed!"
- **Run pylint**: `find . -type f -name "*.py" | xargs python -m pylint --fail-under 7.0 --max-line-length=132` -- takes ~7 seconds. Currently scores 9.01/10 and passes.
- **Format code**: `python -m ruff check --fix --unsafe-fixes` -- takes <1 second. Auto-fixes style issues.
- **Format HTML**: `npx prettier --write api/index.html` -- takes ~2 seconds.

### Docker Environment (Working)
- **Create data directories**: `make datadir` -- takes <1 second.
- **Build images**: `make build_images` -- takes ~30 seconds. NEVER CANCEL.
- **Start services**: `make up` -- will start PostgreSQL and API services. May have permission issues with uv package manager.

### Local Development (Recommended)
- **Run API locally**: `python api/entrypoint.py --port 8080 --workers 4` -- starts the API. Requires bjoern installation.
- **Test API help**: `python api/entrypoint.py --help` -- shows available command line options.
- **API serves web interface**: Visit http://localhost:8080/ to see the HTML interface.

## Validation

### Manual Testing Scenarios
- **ALWAYS run the complete test suite** after making changes: `python -m pytest -vvv` -- should show "100 passed"
- **Test parsing functionality**: `python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('cmc=3'))"` -- should output Query AST structure
- **Test API entry point**: `python api/entrypoint.py --help` -- should show command line options
- **Test API functionality**: Start API with `python api/entrypoint.py --port 8080 --workers 2` then visit http://localhost:8080/ to see web interface
- **BEFORE committing**: Run `python -m ruff check --fix --unsafe-fixes` to fix style issues
- **BEFORE committing**: Run `npx prettier --write api/index.html` to format the HTML frontend.

### Quick Validation Workflow
```bash
# Complete validation in under 30 seconds
python -m pip install -r requirements.txt -r test-requirements.txt
python -m pip install bjoern  # Required for local API testing
python -m pytest -vvv
python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('cmc=3'))"
python -m ruff check
```

### Current Limitations
- **bjoern dependency**: Missing from requirements.txt but needed for local API testing
- **Database integration testing**: Requires running PostgreSQL or Docker compose setup
- The project builds and tests successfully for both local Python development and containerized deployment.

## Common Tasks

### Development Commands That Work
```bash
# Install dependencies (run once)
sudo apt-get update && sudo apt-get install -y libev-dev
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r test-requirements.txt
python -m pip install bjoern  # Required for local API testing

# Test and validate changes
python -m pytest -vvv                    # Run tests (~1 second)
python -m ruff check --fix --unsafe-fixes # Fix style issues (<1 second)
npx prettier --write api/index.html      # Format HTML (~2 seconds)
python api/entrypoint.py --help          # Test API entrypoint

# Docker workflow (works)
make datadir                             # Create data directories (<1 second)
make build_images                        # Build Docker images (~30 seconds)
make up                                  # Start all services (may have permission issues)
```

### Development Commands With Known Issues
```bash
# These commands work but have caveats:
make lint                               # Works but requires installing pylint first
./.github/copilot-setup.sh             # Automated setup script (has permission issues with system packages)
```

### Timing Expectations - NEVER CANCEL
- **System package installation**: 5-7 seconds per package. NEVER CANCEL.
- **Python dependency installation**: 2-4 seconds for standard packages. NEVER CANCEL.
- **bjoern compilation**: ~3 seconds including C compilation. NEVER CANCEL.
- **Unit tests**: ~1 second for full test suite (100 tests).
- **Linting**: <1 second for ruff, ~7 seconds for pylint.
- **Docker builds**: 30-60 seconds when working. NEVER CANCEL.
- **HTML formatting**: ~2 seconds. NEVER CANCEL.

## Project Structure Reference

### Repository Root
```
.
├── README.md                 # Basic project description
├── NOTES.md                 # Development notes
├── makefile                 # Build automation
├── docker-compose.yml       # Container orchestration
├── package.json             # Node.js dependencies (prettier)
├── pyproject.toml           # Python project configuration
├── requirements.txt         # Python runtime dependencies
├── test-requirements.txt    # Python test dependencies
├── api/                     # Python API service
├── client/                  # HTML/JS client
├── configs/                 # Configuration files
└── scripts/                 # Build helper scripts
```

### Key API Files
```
api/
├── entrypoint.py           # Main API server entry point
├── api_resource.py         # Falcon web framework resources
├── api_worker.py           # Multi-process worker implementation
├── index.html              # Web frontend (single file)
├── parsing/                # Scryfall query parser
│   ├── parsing_f.py        # Parser implementation
│   ├── nodes.py            # AST node definitions
│   └── tests/              # Parser unit tests
└── db/                     # Database schema files
```

### GitHub Actions CI
- **Unit tests workflow**: `.github/workflows/unit-tests.yml`
- **Lint workflow**: `.github/workflows/lint.yml` 
- **Runs on every push**: Installs Python 3.11, system deps, Python deps, runs pytest and ruff
- **Expected to pass**: All 100 tests should pass in CI environment
- **Automated setup script**: `.github/copilot-setup.sh` -- automated environment setup (may have permission issues)

## Key Development Notes
- **Database schema**: Complex PostgreSQL schema in `api/db/` with Magic card data structures
- **Query parser**: Implements Scryfall's search DSL in `api/parsing/` 
- **Web framework**: Uses Falcon for lightweight, fast API development
- **Multi-process**: API uses bjoern WSGI server with multiple worker processes (requires separate bjoern installation)
- **Code quality**: Project maintains good code quality with ruff (passing) and pylint (9.01/10 score)
- **Testing**: Excellent test coverage with 100 tests for parsing logic and query translation
- **Web interface**: Single-file HTML/CSS/JS application served by the API at `/`