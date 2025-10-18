# Gatherer Import Module

This module provides functionality to import Magic: The Gathering card data from Gatherer (or Scryfall as a fallback) and organize it into JSON files per set.

## Overview

The Gatherer import module allows you to:
- Fetch card data for specific sets from Gatherer or Scryfall
- Convert card data to a standardized JSON format
- Save card data as individual JSON files organized by set

## Components

### `fetcher.py`
Contains the `GathererFetcher` class that handles:
- Fetching set information from Scryfall API
- Retrieving all cards for a specific set
- Rate limiting and error handling

### `set_converter.py`
Contains the `SetConverter` class that handles:
- Converting card data to JSON format
- Organizing cards by set
- Writing JSON files to disk

### `cli.py`
Command-line interface for easy usage:
```bash
python -m gatherer_import.cli --set DOM --output ./data
```

## Usage

### Basic Usage

```python
from gatherer_import import GathererFetcher, SetConverter

# Initialize fetcher
fetcher = GathererFetcher()

# Fetch all cards from a set
cards = fetcher.fetch_set("DOM")  # Dominaria

# Convert and save
converter = SetConverter(output_dir="./data")
converter.save_set("DOM", cards)
```

### Command Line

```bash
# Fetch a single set
python -m gatherer_import.cli --set DOM

# Fetch multiple sets
python -m gatherer_import.cli --set DOM --set RNA --set WAR

# Fetch all sets
python -m gatherer_import.cli --all-sets

# Specify custom output directory
python -m gatherer_import.cli --set DOM --output /path/to/data
```

## Output Format

Cards are saved as JSON files with the following structure:
```
data/
  DOM/
    DOM.json          # All cards in the set
    metadata.json     # Set metadata
```

Each card in the JSON file includes:
- Name, mana cost, type line, oracle text
- Colors, color identity, keywords
- Power/toughness, loyalty (if applicable)
- Set information, rarity, collector number
- Image URLs
- And all other Scryfall/Gatherer data

## Development

### Running Tests

```bash
python -m pytest gatherer_import/tests/
```

### Adding New Data Sources

To add support for additional data sources:
1. Implement a new fetcher class that inherits from `BaseFetcher`
2. Implement the `fetch_set()` method
3. Register the fetcher in `fetcher.py`

## Notes

- Currently uses Scryfall API as the primary data source
- Rate limiting is implemented to respect API limits (100ms between requests)
- All cards are fetched with filters for paper format and legal cards only
- Future enhancements may include direct Gatherer scraping for official WotC data
