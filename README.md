# Scryfall OS

![Scryfall OS Web Interface](scryfallos-screenshot.png)

*Scryfall OS web interface in dark mode showing cards with CMC less than 10, ordered by USD price descending*

## Table of Contents

1. [Project Overview](#project-overview)
   1. [Legal Notices and Disclaimers](#️-legal-notices-and-disclaimers)
1. [Functionality Comparison](#functionality-comparison)
   1. [Recommended Development Priorities](#recommended-development-priorities)
1. [Code Organization](#code-organization)
1. [Developer Quick Start](#developer-quick-start)
1. [Card Tagging System](#card-tagging-system)
1. [API Documentation](#api-documentation)
1. [Development Notes](#development-notes)
1. [Legal and Compliance](#legal-and-compliance)

## Project Overview

Scryfall OS is an open source implementation of a Magic: The Gathering card search engine, inspired by Scryfall.

### ⚖️ Legal Notices and Disclaimers

**Not Affiliated with Wizards of the Coast**: Scryfall OS is unofficial Fan Content permitted under the Wizards of the Coast Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

**Magic: The Gathering Trademarks**: Wizards of the Coast, Magic: The Gathering, and their logos are trademarks of Wizards of the Coast LLC in the United States and other countries. © 1993-2025 Wizards. All Rights Reserved.

**Not Affiliated with Scryfall**: Scryfall OS is not affiliated with, endorsed by, or sponsored by Scryfall LLC. We use Scryfall's publicly available bulk data API in accordance with their terms of service. "Scryfall OS" indicates this is an "open source" implementation of similar search functionality.

**For Complete Legal Information**: See [LEGAL.md](LEGAL.md), [Terms of Service](TERMS_OF_SERVICE.md), and [Privacy Policy](PRIVACY_POLICY.md).

### Scryfall OS vs Official Scryfall

| Feature                    | Syntax                                        | Scryfall | Scryfall OS | Description                                               |
|----------------------------|-----------------------------------------------|----------|-------------|-----------------------------------------------------------|
| **Basic Search**           | `name:`, `oracle:`                            | ✔        | ✔           | Full substring search with pattern matching               |
| **Type Search**            | `type:`, `t:`                                 | ✔        | ✔           | Exact matching with intelligent autocomplete              |
| **Flavor Text**            | `flavor:`                                     | ✔        | ✔           | Full text search with pattern matching                    |
| **Artist Search**          | `artist:`, `a:`                               | ✔        | ✔           | Full text search with trigram indexing                    |
| **Set Search**             | `set:`, `s:`                                  | ✔        | ✔           | Dedicated indexed column with exact matching              |
| **Rarity Search**          | `rarity:`, `r:`                               | ✔        | ✔           | Integer-based ordering with all comparison operators      |
| **Frame Search**           | `frame:`                                      | ✔        | ✔           | Card frame type and visual properties search              |
| **Watermark Search**       | `watermark:`                                  | ✔        | ✔           | Card watermark and visual properties search               |
| **Mana Production**        | `produces:`                                   | ✔        | ✔           | Search for lands and mana-producing cards                 |
| **Numeric Attributes**     | `cmc:`, `power:`, `toughness:`, `loyalty:`    | ✔        | ✔           | Complete with all comparison operators                    |
| **Colors & Identity**      | `color:`, `identity:`, `c:`, `id:`            | ✔        | ✔           | JSONB-based with complex color logic                      |
| **Pricing Data**           | `usd:`, `eur:`, `tix:`                        | ✔        | ✔           | Complete with all comparison operators                    |
| **Advanced Logic**         | `AND`, `OR`, `NOT`, `()`                      | ✔        | ✔           | Full boolean logic support                                |
| **Keywords**               | `keyword:`                                    | ✔        | ✔           | JSONB object storage                                      |
| **Mana Costs**             | `mana:`, `m:`                                 | ✔        | ✔           | Both JSONB and text representations                       |
| **Oracle Tags**            | `oracle_tags:`, `ot:`                         | ✔        | ✔           | Standard Scryfall feature                                 |
| **Date Search**            | `date:`, `year:`                              | ✔        | ✔           | Card release date filtering with comparison operators     |
| **Devotion Search**        | `devotion:`                                   | ✔        | ✔           | Mana cost devotion calculations with split mana support   |
| **Format Legality**        | `format:`, `legal:`, `banned:`, `restricted:` | ✔        | ✔           | Competitive play support                                  |
| **Collector Numbers**      | `number:`, `cn:`                              | ✔        | ✔           | Card collector number search                              |
| **Card Layout**            | `layout:`                                     | ✔        | ✔           | Card layout types (normal, split, transform, etc.)        |
| **Card Border**            | `border:`                                     | ✔        | ✔           | Border colors (black, white, borderless, etc.)            |
| **Special Properties**     | `is:`                                         | ✔        | ✔           | Card classifications (creature, spell, permanent, etc.)   |
| **Comparison Operators**   | `=`, `<`, `>`, `<=`, `>=`, `!=`, `<>`         | ✔        | ✔           | All comparison operators supported                        |
| **Regular Expressions**    | `/pattern/`                                   | ✔        | ✔           | Pattern matching with regex syntax                        |
| **Collection Features**    | `cube:`, `papersets:`                         | ✔        | ✘           | Collection and cube inclusion features                    |
| **Arithmetic Expressions** | `cmc+1<power`, `power-toughness=0`            | ✘        | ✔           | Advanced mathematical expressions                         |


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

1. Implement a default prefer order
1. Support for double faced cards
1. More comprehensive tagging info - per card, per card-printing, per artwork
1. `cube:`, `papersets:`


### Missing Functionality - Complexity vs Impact Grid

Based on [comprehensive functionality analysis](docs/scryfall_functionality_analysis.md), here's the updated priority matrix:

| **Complexity** | **Low Impact**                | **Medium Impact**                                                                            | **High Impact** |
| -------------- | ------------------------------|----------------------------------------------------------------------------------------------| ----------------|
| **Low**        | **Cube Inclusion** (`cube:`)  |                                                                                              |                 |
| **Medium**     |                               | **Reprint Info** (`papersets:`) - [Scryfall Docs](https://scryfall.com/docs/syntax#reprints) |                 |
| **High**       |                               |                                                                                              |                 |

### Implementation Status

- **Current API Success Rate**: 100% for supported features (enhanced coverage with flavor text search)
- **Test Coverage**: 622 total tests including 426 parser tests with comprehensive validation
- **Performance**: Optimized PostgreSQL with proper indexing including full-text search capabilities
- **Data Quality**: Regular comparison testing against official Scryfall API

## Code Organization

```
scryfallos/
├── api/                         # Python API service (main application)
│   ├── db/                      # Database schema and migrations
│   ├── middlewares/             # HTTP middleware components
│   ├── parsing/                 # Query parser implementation
│   │   ├── tests/               # Parser unit tests (100+ tests)
│   │   ├── nodes.py             # AST node definitions
│   │   ├── parsing_f.py         # Main parser with pyparsing
│   │   └── scryfall_nodes.py    # Scryfall-specific node types
│   ├── sql/                     # SQL query templates
│   ├── tests/                   # Integration and API tests
│   ├── api_resource.py          # Falcon web framework resources
│   ├── api_worker.py            # Multi-process worker implementation
│   ├── entrypoint.py            # API server entry point and CLI
│   └── index.html               # Web frontend (single-file app)
├── client/                      # Client-side assets (minimal)
├── configs/                     # Configuration files
├── docs/                        # Project documentation and analysis
├── requirements/                # Requirements files
│   ├── base.txt                 # base requirements
│   ├── test.txt                 # testing requirements
│   └── webserver.txt            # webserver requirements - requires building libev
├── scripts/                     # Utility and maintenance scripts
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
   python -m pip install -r requirements/base.txt -r requirements/test.txt
   ```

1. **Optional: Web Server Dependencies**

   ```bash
   # Only needed for local API server (includes bjoern compilation)
   sudo apt-get update && sudo apt-get install -y libev-dev
   python -m pip install -r requirements/webserver.txt
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

## Legal and Compliance

### Intellectual Property Acknowledgments

**Wizards of the Coast**: Wizards of the Coast, Magic: The Gathering, and their logos are trademarks of Wizards of the Coast LLC in the United States and other countries. © 1993-2025 Wizards. All Rights Reserved.

**Full Legal Notice**: Scryfall OS is not affiliated with, endorsed, sponsored, or specifically approved by Wizards of the Coast LLC. Scryfall OS may use the trademarks and other intellectual property of Wizards of the Coast LLC, which is permitted under Wizards' Fan Content Policy. MAGIC: THE GATHERING® is a trademark of Wizards of the Coast. For more information about Wizards of the Coast or any of Wizards' trademarks or other intellectual property, please visit their website at www.wizards.com.

**Scryfall Data**: This project uses data provided by [Scryfall LLC](https://scryfall.com/). We are not affiliated with Scryfall LLC. Card data is obtained through Scryfall's publicly available bulk data API in compliance with their terms of service.

### Data Sources and Attribution

- **Card Data**: Official Wizards of the Coast data via Scryfall's bulk data API
- **Card Images**: Sourced from official Wizards servers and Scryfall's image CDN
- **Price Data**: Aggregated from third-party market data sources via Scryfall

See [LEGAL.md](LEGAL.md) for complete information about:
- Our compliance with the Wizards of the Coast Fan Content Policy
- Data sources and attribution requirements
- Relationship to Scryfall and Wizards of the Coast
- Font and asset usage
- Third-party library licenses

### Policies and Documentation

- **[LEGAL.md](LEGAL.md)** - Complete legal documentation and compliance information
- **[Terms of Service](TERMS_OF_SERVICE.md)** - Terms governing use of this service
- **[Privacy Policy](PRIVACY_POLICY.md)** - How we handle data and privacy

### Project License

This project's original code is released under the MIT License. See [LICENSE](LICENSE) file for details.

**Important**: The MIT License applies only to our original code, not to:
- Magic: The Gathering content, trademarks, or copyrights (owned by Wizards of the Coast)
- Data obtained from Scryfall (subject to their terms)
- Third-party libraries (subject to their own licenses)

### Contact

For legal inquiries or concerns:
- **GitHub Issues**: https://github.com/jbylund/arcane_tutor/issues
- **Project Repository**: https://github.com/jbylund/arcane_tutor
