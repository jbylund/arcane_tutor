# Scryfall OS

![Scryfall OS Web Interface](scryfallos-screenshot.png)

*Scryfall OS web interface in dark mode showing cards with CMC less than 10, ordered by USD price descending*

## Table of Contents

1. [Project Overview](#project-overview)
2. [Functionality Comparison](#functionality-comparison)
3. [Code Organization](#code-organization)
4. [Developer Quick Start](#developer-quick-start)
5. [Card Tagging System](#card-tagging-system)
6. [API Documentation](#api-documentation)
7. [Development Notes](#development-notes)

## Project Overview

Scryfall OS is an open source implementation of Scryfall, a Magic: The Gathering card search engine. This project provides a complete card database and search system that mirrors many of Scryfall's features while adding unique extensions like Oracle tags and comprehensive card tagging.

### Core Components

1. **Search DSL Parser** - A comprehensive parsing library for Scryfall's query syntax supporting text search, numeric comparisons, color identity, and advanced operators
2. **Database Query Engine** - Converts parsed queries into optimized PostgreSQL queries with support for complex joins and filtering
3. **Data Import Tools** - Bulk data loading from Scryfall exports with incremental updates and card tagging integration
4. **Web Interface** - A responsive HTML/JavaScript application providing search functionality with card display similar to Scryfall
5. **Card Tagging System** - Extended functionality for importing and managing Scryfall's card tags with hierarchy support
6. **RESTful API** - Falcon-based web service with multi-process worker support and comprehensive search endpoints

### Key Features

- **Full Scryfall Syntax Support**: Implements core search functionality including `name:`, `oracle:`, `type:`, `set:`, `s:`, `artist:`, `rarity:`, `r:`, `cmc:`, `power:`, `color:`, `identity:`, `number:`, `cn:`, pricing data (`usd:`, `eur:`, `tix:`), and arithmetic operations
- **Advanced Search Operations**: Supports complex queries with `AND`, `OR`, `NOT` logic, parenthetical grouping, and arithmetic expressions like `cmc+1<power`
- **Oracle Tags Extension**: Enhanced tagging system with hierarchy support and bulk import capabilities
- **Performance Optimized**: PostgreSQL backend with proper indexing and query optimization for fast search results
- **Docker Ready**: Complete containerization with Docker Compose for easy deployment and development

## Functionality Comparison

### Recommended Development Priorities

1. **High Impact, Low Complexity** - 🎯 **Next Priority**: Special properties (`is:`, `produces:`) and card visual properties for enhanced search capabilities
2. **High Impact, Medium Complexity** - Advanced mechanics and devotion calculations for specialized gameplay searches  
3. **Medium Impact Features** - Layout, dates, and planeswalker loyalty for comprehensive card metadata coverage
4. **Low Impact Features** - Watermarks, flavor text, and advanced pattern matching for completeness

**Recently Completed ✅:**
- Format legality (`format:`, `legal:`, `banned:`) for competitive play support
- Collector numbers (`number:`, `cn:`) and rarity search (`rarity:`, `r:`) 
- Pricing data (`usd:`, `eur:`, `tix:`) for market analysis
- Artist search (`artist:`, `a:`) with trigram indexing
- Flavor text search (`flavor:`) for searching card flavor text
- Card frame search (`frame:`) for frame type and visual properties
- Mana production search (`produces:`) for lands and mana-producing cards

### Missing Functionality - Complexity vs Impact Grid

Based on [comprehensive functionality analysis](docs/scryfall_functionality_analysis.md), here's the updated priority matrix:

| **Complexity** | **Low Impact**                                                   | **Medium Impact**                                                                            | **High Impact**                                                                                   |
| -------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| **Low**        | **Watermark** (`watermark:`)                                    | **Layout** (`layout:`)<br/>**Border** (`border:`)                                           | **Special Properties** (`is:`)<br/>**Card Visual Properties**                                     |
| **Medium**     | **Cube Inclusion** (`cube:`)<br/>**Commander Features** (`cmd:`) | **Release Dates** (`year:`, `date:`)<br/>**Planeswalker Loyalty** (`loyalty:`)               | **Devotion** (`devotion:`)        |
| **High**       | **Regular Expressions** (`/pattern/`)    | **Advanced Functions** (`max:`, `min:`)<br/>**Paper Sets** (`papersets:`)                   | **Complex Game Rules** (`is:split`, `is:modal`)<br/>**Meta Properties** (`is:booster`)            |

### Implementation Status

- **Current API Success Rate**: 100% for supported features (enhanced coverage with flavor text search)
- **Test Coverage**: 376 total tests including 299 parser tests with comprehensive validation
- **Performance**: Optimized PostgreSQL with proper indexing including full-text search capabilities
- **Data Quality**: Regular comparison testing against official Scryfall API

### Scryfall OS vs Official Scryfall

#### Fully Supported Features ✅

| Feature                | Syntax                             | Status                                               |
| ---------------------- | ---------------------------------- | ---------------------------------------------------- |
| **Basic Search**       | `name:`, `oracle:`                 | Full substring search with pattern matching          |
| **Type Search**        | `type:`, `t:`                      | Exact matching with intelligent autocomplete         |
| **Flavor Text**        | `flavor:`                          | Full text search with pattern matching               |
| **Artist Search**      | `artist:`, `a:`                    | Full text search with trigram indexing               |
| **Set Search**         | `set:`, `s:`                       | Dedicated indexed column with exact matching         |
| **Rarity Search**      | `rarity:`, `r:`                    | Integer-based ordering with all comparison operators |
| **Frame Search**       | `frame:`                           | Card frame type and visual properties search         |
| **Mana Production**    | `produces:`                        | Search for lands and mana-producing cards            |
| **Numeric Attributes** | `cmc:`, `power:`, `toughness:`     | Complete with all comparison operators               |
| **Colors & Identity**  | `color:`, `identity:`, `c:`, `id:` | JSONB-based with complex color logic                 |
| **Pricing Data**       | `usd:`, `eur:`, `tix:`             | Complete with all comparison operators               |
| **Advanced Logic**     | `AND`, `OR`, `NOT`, `()`           | Full boolean logic support                           |
| **Arithmetic**         | `cmc+1<power`, `power-toughness=0` | Advanced mathematical expressions                    |
| **Keywords**           | `keyword:`                         | JSONB object storage                                 |
| **Mana Costs**         | `mana:`, `m:`                      | Both JSONB and text representations                  |
| **Oracle Tags**        | `oracle_tags:`, `ot:`              | Scryfall OS unique extension                         |

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

2. **Optional: Web Server Dependencies**

   ```bash
   # Only needed for local API server (includes bjoern compilation)
   sudo apt-get update && sudo apt-get install -y libev-dev
   python -m pip install -r webserver-requirements.txt
   ```

3. **Validate Installation**

   ```bash
   # Run test suite (should pass all 339 tests)
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
2. **magic.card_tags** table - Stores tag hierarchy with parent-child relationships

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
- **GET /get_all_tags** - Retrieve all available tags

### Query Parameters

The search endpoint supports comprehensive Scryfall syntax. See [syntax analysis](docs/scryfall_syntax_analysis.md) for complete documentation.

## Development Notes

### Current Limitations

- **Missing Features**: See functionality grid above for complete list
- **Data Source**: Currently uses `oracle_cards` bulk data; may migrate to `default_cards`

### Future Enhancements

1. **Database Migration**: Evaluate `default_cards` vs `oracle_cards` for improved completeness
2. **Features**: Implement highest-priority missing functionality from grid above
3. **Testing**: Expand API comparison coverage and add performance benchmarks

For detailed technical analysis, see [functionality analysis documentation](docs/scryfall_functionality_analysis.md).
