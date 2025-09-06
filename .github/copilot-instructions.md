# Scryfall OS

Scryfall OS is an open source implementation of Scryfall, a Magic: The Gathering card search engine. This project consists of a Python-based API service, a simple HTML/JavaScript web client, and a PostgreSQL database, designed to be deployed via Docker Compose.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Firewall and Network Considerations

### Known Firewall Blocks in Sandboxed Environments
When running in Copilot coding agent sandboxed environments, the following network access may be blocked:
- `esm.ubuntu.com` - Ubuntu Extended Security Maintenance (during `sudo apt-get update`)
- `pypi.org` - Python Package Index timeouts during pip installs

### Workarounds for Blocked Access
1. **System packages**: Use `sudo apt-get install` directly without `sudo apt-get update`
2. **Python packages**: Use system packages (`sudo apt-get install python3-<package>`) as fallback
3. **GitHub Actions setup**: Configure Actions setup steps to install dependencies before firewall activation
4. **Repository allowlist**: Admins can add blocked URLs to [Copilot coding agent settings](https://github.com/jbylund/scryfallos/settings/copilot/coding_agent)

### Recommended Setup Sequence for Copilot Agents
```bash
# 1. Try firewall-aware installation first
sudo apt-get install -y libev-dev
python -m pip install --upgrade pip

# 2. Attempt pip install with timeout
python -m pip install -r requirements.txt -r test-requirements.txt --timeout 60

# 3. If step 2 fails, use system package fallback
sudo apt-get install -y python3-pytest python3-falcon python3-requests

# 4. Test basic functionality
python -m pytest -vvv || echo "Some tests may fail without full pip requirements"
```

## Working Effectively

### Prerequisites and Environment Setup

#### Firewall-Aware Installation (Recommended for Copilot Agents)
Due to firewall restrictions in sandboxed environments that block `esm.ubuntu.com` and may cause timeouts with `pypi.org`, use this approach:

- Install system dependencies (skipping update to avoid firewall blocks):
  - `sudo apt-get install -y libev-dev` -- takes ~12 seconds. NEVER CANCEL. (Works without update)
- Install Python dependencies with retries and fallbacks:
  - `python -m pip install --upgrade pip` -- takes ~3 seconds.
  - Try: `python -m pip install -r requirements.txt -r test-requirements.txt --timeout 60` -- takes ~9 seconds when working.
  - If blocked/timeout: Use system packages: `sudo apt-get install -y python3-pytest python3-falcon python3-requests`

#### Standard Installation (for environments without firewall restrictions)
- Install system dependencies:
  - `sudo apt-get update` -- takes ~11 seconds. NEVER CANCEL.
  - `sudo apt-get install -y libev-dev` -- takes ~12 seconds. NEVER CANCEL.
- Install Python dependencies:
  - `python -m pip install --upgrade pip` -- takes ~3 seconds.
  - `python -m pip install -r requirements.txt -r test-requirements.txt` -- takes ~9 seconds. Includes compilation of bjoern C extension.

### Build and Test Workflow
- **Run tests**: `python -m pytest -vvv` -- takes ~1 second. All 58 tests should pass.
- **Run linting**: `make lint` -- takes ~6 seconds but CURRENTLY FAILS with 140+ violations. The project has many missing docstrings and code style issues that need to be fixed before this succeeds.
- **Format HTML**: `npx prettier --write api/index.html` -- takes ~3 seconds.

### Docker Environment (Currently Non-Functional)
- **CRITICAL**: Docker builds FAIL in sandboxed environments due to SSL certificate issues when downloading pip.
- **Create data directories**: `make datadir` -- takes <1 second.
- **Build images**: `make build_images` -- FAILS after ~1 minute due to curl SSL certificate problems.
- **Start services**: `make up` -- will fail because image build fails.

### Local Development (Recommended)
- **Run API locally**: `python api/entrypoint.py --port 8080 --workers 4` -- requires full pip requirements (bjoern C extension). Use firewall-aware installation first.
- **Test API help**: `python api/entrypoint.py --help` -- requires full pip requirements, will fail with system packages only due to missing bjoern.

## Validation

### Manual Testing Scenarios
- **ALWAYS run the complete test suite** after making changes: `python -m pytest -vvv` -- should show "58 passed"
- **Test parsing functionality**: `python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('cmc=3'))"` -- should output Query AST structure
- **Test API entry point**: `python api/entrypoint.py --help` -- requires full pip requirements (bjoern), will fail with system packages only
- **BEFORE committing**: Run `make lint` but expect it to fail until code style violations are fixed.
- **BEFORE committing**: Run `npx prettier --write api/index.html` to format the HTML frontend.

### Quick Validation Workflow
```bash
# Complete validation in under 30 seconds (firewall-aware)
sudo apt-get install -y libev-dev python3-pytest python3-falcon python3-requests
python -m pytest -vvv
python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('cmc=3'))"
```

#### Fallback for Firewall-Blocked Environments
If pip installations fail due to network timeouts:
```bash
# Use system packages as fallback
sudo apt-get install -y libev-dev python3-pytest python3-falcon python3-requests python3-psycopg2
# Note: Some tests may not work without full pip requirements, but basic functionality will work
```

### Current Limitations
- **Docker development workflow is broken** due to SSL certificate issues in containerized environment.
- **Linting fails** - there are 140+ violations that need to be addressed before the lint target passes.
- **Database integration testing** requires fixing Docker build issues or setting up PostgreSQL separately.
- **Firewall restrictions in sandboxed environments** block access to `esm.ubuntu.com` (during apt-get update) and may timeout on `pypi.org`. Use firewall-aware installation methods.
- The project builds and tests successfully for local Python development but not for containerized deployment.

## Common Tasks

### Development Commands That Work
```bash
# Firewall-aware install dependencies (recommended for Copilot agents)
sudo apt-get install -y libev-dev                # Install system deps (~12 seconds, avoids firewall blocks)
python -m pip install --upgrade pip              # Upgrade pip (~3 seconds)
python -m pip install -r requirements.txt -r test-requirements.txt --timeout 60  # Install Python deps (~9 seconds)

# Fallback if pip is blocked: use system packages
sudo apt-get install -y python3-pytest python3-falcon python3-requests

# Test and validate changes
python -m pytest -vvv                    # Run tests (~1 second)
npx prettier --write api/index.html      # Format HTML (~3 seconds)
python api/entrypoint.py --help          # Test API entrypoint

# Create required directories
make datadir                             # Create data directories (<1 second)
```

### Development Commands That Work (Non-Sandboxed Environments)
```bash
# Full install including apt-get update (works when firewall allows)
sudo apt-get update && sudo apt-get install -y libev-dev
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r test-requirements.txt

# All other commands same as above
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
- **Expected to pass**: All 58 tests should pass in CI environment (CI has unrestricted network access)

## Key Development Notes
- **Database schema**: Complex PostgreSQL schema in `api/db/` with Magic card data structures
- **Query parser**: Implements Scryfall's search DSL in `api/parsing/` 
- **Web framework**: Uses Falcon for lightweight, fast API development
- **Multi-process**: API uses bjoern WSGI server with multiple worker processes
- **Code quality**: Project needs significant cleanup - many missing docstrings and style violations
- **Testing**: Good test coverage for parsing logic, but limited integration testing