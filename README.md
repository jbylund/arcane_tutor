# Scryfall OS

![Scryfall OS Web Interface](scryfallos-screenshot.png)

*Scryfall OS web interface in dark mode showing cards with CMC less than 10, ordered by USD price descending*

## Table of Contents

1. [Project Overview](#project-overview)
1. [Functionality Comparison](#functionality-comparison)
   1. [Recommended Development Priorities](#recommended-development-priorities)
1. [Code Organization](#code-organization)
1. [Developer Quick Start](#developer-quick-start)
1. [Card Tagging System](#card-tagging-system)
1. [API Documentation](#api-documentation)
1. [Development Notes](#development-notes)

## Project Overview

Scryfall OS is an open source implementation of Scryfall, a Magic: The Gathering card search engine.

### Scryfall OS vs Official Scryfall

<div style="overflow-x: auto;">

| Feature                    | Syntax                                        | Scryfall | Scryfall OS | Description                                                                 |
|----------------------------|-----------------------------------------------|----------|-------------|-----------------------------------------------------------------------------|
| **Basic Search**           | `name:`, `oracle:`                            | ✔        | ✔           | Full substring search with pattern matching                                 |
| **Type Search**            | `type:`, `t:`                                 | ✔        | ✔           | Exact matching with intelligent autocomplete                                |
| **Flavor Text**            | `flavor:`                                     | ✔        | ✔           | Full text search with pattern matching                                      |
| **Artist Search**          | `artist:`, `a:`                               | ✔        | ✔           | Full text search with trigram indexing                                      |
| **Set Search**             | `set:`, `s:`                                  | ✔        | ✔           | Dedicated indexed column with exact matching                                |
| **Rarity Search**          | `rarity:`, `r:`                               | ✔        | ✔           | Integer-based ordering with all comparison operators                        |
| **Frame Search**           | `frame:`                                      | ✔        | ✔           | Card frame type and visual properties search                                |
| **Watermark Search**       | `watermark:`                                  | ✔        | ✔           | Card watermark and visual properties search                                 |
| **Mana Production**        | `produces:`                                   | ✔        | ✔           | Search for lands and mana-producing cards                                   |
| **Numeric Attributes**     | `cmc:`, `power:`, `toughness:`, `loyalty:`    | ✔        | ✔           | Complete with all comparison operators                                      |
| **Colors & Identity**      | `color:`, `identity:`, `c:`, `id:`            | ✔        | ✔           | JSONB-based with complex color logic                                        |
| **Pricing Data**           | `usd:`, `eur:`, `tix:`                        | ✔        | ✔           | Complete with all comparison operators                                      |
| **Advanced Logic**         | `AND`, `OR`, `NOT`, `()`                      | ✔        | ✔           | Full boolean logic support                                                  |
| **Keywords**               | `keyword:`                                    | ✔        | ✔           | JSONB object storage                                                        |
| **Mana Costs**             | `mana:`, `m:`                                 | ✔        | ✔           | Both JSONB and text representations                                         |
| **Oracle Tags**            | `oracle_tags:`, `ot:`                         | ✔        | ✔           | Standard Scryfall feature                                                   |
| **Date Search**            | `date:`, `year:`                              | ✔        | ✔           | Card release date filtering with comparison operators                       |
| **Devotion Search**        | `devotion:`                                   | ✔        | ✔           | Mana cost devotion calculations with split mana support                     |
| **Format Legality**        | `format:`, `legal:`, `banned:`, `restricted:` | ✔        | ✔           | Competitive play support                                                    |
| **Collector Numbers**      | `number:`, `cn:`                              | ✔        | ✔           | Card collector number search                                                |
| **Card Layout**            | `layout:`                                     | ✔        | ✔           | Card layout types (normal, split, transform, etc.)                          |
| **Card Border**            | `border:`                                     | ✔        | ✔           | Border colors (black, white, borderless, etc.)                              |
| **Special Properties**     | `is:`                                         | ✔        | ✔           | Card classifications (creature, spell, permanent, etc.)                     |
| **Comparison Operators**   | `=`, `<`, `>`, `<=`, `>=`, `!=`, `<>`         | ✔        | ✔           | All comparison operators supported                                          |
| **Collection Features**    | `cube:`, `papersets:`                         | ✔        | ✘           | Collection and cube inclusion features                                      |
| **Regular Expressions**    | `/pattern/`                                   | ✔        | ✘           | Pattern matching with regex syntax                                          |
| **Arithmetic Expressions** | `cmc+1<power`, `power-toughness=0`            | ✘        | ✔           | Advanced mathematical expressions                                           |

</div>

<style>
table tr:nth-child(27) { background-color: #f8d7da; } /* Collection Features - red (✔✘) */
table tr:nth-child(28) { background-color: #f8d7da; } /* Regular Expressions - red (✔✘) */
table tr:nth-child(29) { background-color: #d1ecf1; } /* Arithmetic Expressions - blue (✘✔) */
</style>

### Scryfall OS Unique Features

- **Arithmetic operations** - Mathematical expressions like `cmc+1<power`
- **Typeahead search with intelligent completion** - Enhanced UX for query building
- **Optimized database schema for low latency queries** - Performance improvements
- **Larger data fetch capabilities** - No 175 card/page limit like Scryfall
- **Data synchronization tools** - Tools to sync from upstream Scryfall
- **Local deployment** - Run your own instance with Docker

### Core Components

1. **Search DSL Parser** - A comprehensive parsing library for Scryfall's query syntax supporting text search, numeric comparisons, color identity, and advanced operators
1. **Database Query Engine** - Converts parsed queries into optimized PostgreSQL queries with support for complex joins and filtering
1. **Data Import Tools** - Bulk data loading from Scryfall exports with incremental updates and card tagging integration
1. **Web Interface** - A responsive HTML/JavaScript application providing search functionality with card display similar to Scryfall
1. **Card Tagging System** - Extended functionality for importing and managing Scryfall's card tags with hierarchy support
1. **RESTful API** - Falcon-based web service with multi-process worker support and comprehensive search endpoints

## Functionality Comparison

### Recommended Development Priorities

1. **Low Impact Features** - regex based search


### Missing Functionality - Complexity vs Impact Grid

Based on [comprehensive functionality analysis](docs/scryfall_functionality_analysis.md), here's the updated priority matrix:

| **Complexity** | **Low Impact**                            | **Medium Impact**                                                                            | **High Impact**                                                                                   |
| -------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------- |
| **Low**        | **Cube Inclusion** (`cube:`)              |                                                                                              |                              |
| **Medium**     |         | **Reprint Info** (`papersets:`) - [Scryfall Docs](https://scryfall.com/docs/syntax#reprints) |                              |
| **High**       | **Regular Expressions** (`/pattern/`)     |                                                                                              |                              |

### Implementation Status

- **Current API Success Rate**: 100% for supported features (enhanced coverage with flavor text search)
- **Test Coverage**: 622 total tests including 426 parser tests with comprehensive validation
- **Performance**: Optimized PostgreSQL with proper indexing including full-text search capabilities
- **Data Quality**: Regular comparison testing against official Scryfall API

## Code Organization

```
scryfallos/
├── api/                          # Python API service (main application)
│   ├── entrypoint.py            # API server entry point and CLI
│   ├── api_resource.py          # Falcon web framework resources
│   ├── api_worker.py            # Multi-process worker implementation
│   ├── index.html               # Web frontend (single-file app)
│   ├── parsing/                 # Query parser implementation
│   │   ├── parsing_f.py         # Main parser with pyparsing
│   │   ├── nodes.py             # AST node definitions
│   │   ├── scryfall_nodes.py    # Scryfall-specific node types
│   │   └── tests/               # Parser unit tests (100+ tests)
│   ├── db/                      # Database schema and migrations
│   ├── sql/                     # SQL query templates
│   ├── middlewares/             # HTTP middleware components
│   └── tests/                   # Integration and API tests
├── scripts/                     # Utility and maintenance scripts
├── docs/                        # Project documentation and analysis
├── client/                      # Client-side assets (minimal)
├── configs/                     # Configuration files
├── requirements.txt             # Core Python dependencies
├── test-requirements.txt        # Testing dependencies
├── webserver-requirements.txt   # Optional web server dependencies
├── docker-compose.yml           # Container orchestration
└── makefile                     # Build automation
```

### Specialized Documentation

- **[Scripts Documentation](scripts/README.md)** - Detailed information about utility scripts including the Scryfall comparison tool
- **[API Tests Documentation](api/tests/README.md)** - Testing framework and integration test information
- **[CI/CD Workflows](docs/workflows/README_CI_MONITOR.md)** - Continuous integration and monitoring documentation

## Developer Quick Start

### Prerequisites

- Python 3.13+ (tested with 3.13)
- PostgreSQL 17+ (for full functionality)
- Docker and Docker Compose (for containerized development)
- Node.js (for HTML formatting tools)

### Setup Instructions

1. **Clone and Install Dependencies**

   ```bash
   git clone git@github.com:jbylund/scryfallos.git
   cd scryfallos

   # Install core dependencies
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt -r test-requirements.txt
   ```

1. **Optional: Web Server Dependencies**

   ```bash
   # Only needed for local API server (includes bjoern compilation)
   sudo apt-get update && sudo apt-get install -y libev-dev
   python -m pip install -r webserver-requirements.txt
   ```

1. **Validate Installation**

   ```bash
   # Run test suite (should pass all 622 tests)
   python -m pytest -vvv

   # Verify linting
   python -m ruff check

   # Test parser functionality including rarity search
   python -c "from api.parsing import parse_scryfall_query; print(parse_scryfall_query('rarity>uncommon'))"
   ```

### Development Workflows

#### Docker Development (Recommended)

```bash
# Quick start - just run this!
make up              # Creates directories, builds images, starts all services

# Or step by step:
make datadir          # Create data directories
make build_images     # Build Docker images (~30-60 seconds)
make up              # Start PostgreSQL and API services
```

#### Local Development

```bash
# Start API server locally
python -m api.entrypoint --port 8080 --workers 2

# Visit web interface
open http://localhost:8080/
```

#### Testing and Quality Assurance

```bash
# Run specific test suites
make test            # All tests
make test-unit       # Unit tests only
make test-integration # Integration tests (requires Docker)

# Code quality
make lint            # Run ruff and pylint
python -m ruff check --fix --unsafe-fixes  # Auto-fix style issues
npx prettier --write api/index.html        # Format frontend code
```

### Development Tips

- **Fast validation cycle**: `python -m pytest -vvv && python -m ruff check` (completes in ~2 seconds)
- **Parser testing**: Use `api/parsing/tests/` for comprehensive query parser validation
- **Database connection**: Use `make dbconn` to connect to local PostgreSQL instance
- **API comparison**: Run `python scripts/scryfall_comparison_script.py` to compare against official Scryfall API

## Card Tagging System

The API supports importing and managing card tags from Scryfall's tagger system:

### Available Endpoints

- `update_tagged_cards` - Import cards for a specific tag
- `discover_and_import_all_tags` - Bulk import all available tags and their card associations

### Usage Examples

```bash
# Import cards for a specific tag
curl "http://localhost:8080/update_tagged_cards?tag=flying"

# Discover and import all tags (cards only, no hierarchy)
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=true&import_hierarchy=false"

# Import tag hierarchy only (no card associations)
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=false&import_hierarchy=true"

# Full import: all tags, cards, and hierarchy relationships
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=true&import_hierarchy=true"
```

### Database Schema

The tagging system uses two main database components:

1. **magic.cards.card_tags** (jsonb) - Stores tag associations for each card
1. **magic.card_tags** table - Stores tag hierarchy with parent-child relationships

### Rate Limiting

The bulk import includes built-in rate limiting:

- 200ms delay between individual tag imports
- 500ms delay between hierarchy relationship requests
- Progress logging every 50 tags processed

## API Documentation

### Search Endpoints

- **GET /** - Web interface (serves `index.html`)
- **GET /search** - Card search with query parameter support
- **GET /favicon.ico** - Favicon for web interface

### Tagging Endpoints

- **GET /update_tagged_cards** - Import cards for specific tags
- **GET /discover_and_import_all_tags** - Bulk tag discovery and import

### Query Parameters

The search endpoint supports comprehensive Scryfall syntax. See [syntax analysis](docs/scryfall_syntax_analysis.md) for complete documentation.

## Development Notes

### Current Limitations

- **Missing Features**: See functionality grid above for complete list

### Future Enhancements

1. **Features**: Implement highest-priority missing functionality from grid above
1. **Testing**: Expand API comparison coverage and add performance benchmarks

For detailed technical analysis, see [functionality analysis documentation](docs/scryfall_functionality_analysis.md).
