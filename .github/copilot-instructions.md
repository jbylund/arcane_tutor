# Scryfall OS

Scryfall OS is an open source implementation of Scryfall, a Magic: The Gathering card search engine. This project consists of a Python-based API service, a simple HTML/JavaScript web client, and a PostgreSQL database, designed to be deployed via Docker Compose.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

### Prerequisites and Environment Setup
- Install system dependencies:
  - `sudo apt-get update` -- takes ~11 seconds. NEVER CANCEL.
  - `sudo apt-get install -y libev-dev` -- takes ~12 seconds. NEVER CANCEL.
- Install Python dependencies:
  - `python -m pip install --upgrade pip` -- takes ~3 seconds.
  - `python -m pip install -r requirements.txt -r test-requirements.txt` -- takes ~9 seconds. Includes compilation of bjoern C extension.

### Build and Test Workflow
- **Run tests**: `python -m pytest -vvv` -- takes ~1 second. All 54 tests should pass.
- **Run linting**: `make lint` -- takes ~6 seconds but CURRENTLY FAILS with 140+ violations. The project has many missing docstrings and code style issues that need to be fixed before this succeeds.
- **Format HTML**: `npx prettier --write api/index.html` -- takes ~3 seconds.

### Docker Environment (Currently Non-Functional)
- **CRITICAL**: Docker builds FAIL in sandboxed environments due to SSL certificate issues when downloading pip.
- **Create data directories**: `make datadir` -- takes <1 second.
- **Build images**: `make build_images` -- FAILS after ~1 minute due to curl SSL certificate problems.
- **Start services**: `make up` -- will fail because image build fails.

### Local Development (Recommended)
- **Run API locally**: `python api/entrypoint.py --port 8080 --workers 4` -- starts the API without database dependency for basic functionality testing.
- **Test API help**: `python api/entrypoint.py --help` -- shows available command line options.

## Validation

### Manual Testing Scenarios
- **ALWAYS run the complete test suite** after making changes: `python -m pytest -vvv`
- Test parsing functionality by importing and using the parsing library: `python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('cmc=3'))"`
- **BEFORE committing**: Run `make lint` but expect it to fail until code style violations are fixed.
- **BEFORE committing**: Run `npx prettier --write api/index.html` to format the HTML frontend.

### Current Limitations
- **Docker development workflow is broken** due to SSL certificate issues in containerized environment.
- **Linting fails** - there are 140+ violations that need to be addressed before the lint target passes.
- **Database integration testing** requires fixing Docker build issues or setting up PostgreSQL separately.
- The project builds and tests successfully for local Python development but not for containerized deployment.

## Common Tasks

### Development Commands That Work
```bash
# Install dependencies (run once)
sudo apt-get update && sudo apt-get install -y libev-dev
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r test-requirements.txt

# Test and validate changes
python -m pytest -vvv                    # Run tests (~1 second)
npx prettier --write api/index.html      # Format HTML (~3 seconds)
python api/entrypoint.py --help          # Test API entrypoint

# Create required directories
make datadir                             # Create data directories (<1 second)
```

### Development Commands That Currently Fail
```bash
# These commands will fail until underlying issues are resolved:
make lint                               # Fails with 140+ style violations
make build_images                       # Fails with SSL certificate errors  
make up                                 # Fails because build_images fails
```

### Timing Expectations - NEVER CANCEL
- **System package installation**: 10-15 seconds per package. NEVER CANCEL.
- **Python dependency installation**: 8-10 seconds including C compilation. NEVER CANCEL.
- **Unit tests**: ~1 second for full test suite.
- **Linting**: ~6 seconds when it runs (currently fails).
- **Docker builds**: Expected 2-5 minutes when working. NEVER CANCEL.

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
- **Runs on every push**: Installs Python 3.11, system deps, Python deps, runs pytest
- **Expected to pass**: All 54 tests should pass in CI environment

## Key Development Notes
- **Database schema**: Complex PostgreSQL schema in `api/db/` with Magic card data structures
- **Query parser**: Implements Scryfall's search DSL in `api/parsing/` 
- **Web framework**: Uses Falcon for lightweight, fast API development
- **Multi-process**: API uses bjoern WSGI server with multiple worker processes
- **Code quality**: Project needs significant cleanup - many missing docstrings and style violations
- **Testing**: Good test coverage for parsing logic, but limited integration testing