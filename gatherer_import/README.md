# Gatherer Import

This module provides functionality to import Magic: The Gathering card data directly from Wizards of the Coast's Gatherer website.

## Features

- Fetch card data from specific sets
- Enumerate all available sets
- Save data as JSON files (one file per set)
- Command-line interface for easy use

## Usage

### Command Line Interface

The module can be run as a command-line tool using `python -m gatherer_import`.

#### List all available sets

```bash
python -m gatherer_import list-sets
```

Save the list to a file:

```bash
python -m gatherer_import list-sets --output sets.json
```

#### Fetch a specific set

```bash
python -m gatherer_import fetch-set TDM
```

Specify output directory:

```bash
python -m gatherer_import fetch-set TDM --output my_data/
```

#### Fetch all sets

```bash
python -m gatherer_import fetch-all
```

Specify output directory:

```bash
python -m gatherer_import fetch-all --output gatherer_data/
```

### Python API

You can also use the module programmatically:

```python
from gatherer_import.fetch_gatherer_data import GathererFetcher

# Initialize the fetcher
fetcher = GathererFetcher()

# Fetch a specific set
tdm_cards = fetcher.fetch_set("TDM")

# Get all available sets
all_sets = fetcher.fetch_all_sets()

# Fetch and save a set to JSON
output_file = fetcher.save_set_to_json("TDM", output_dir="my_data")
print(f"Saved to {output_file}")
```

## Data Format

The data is fetched directly from Gatherer's internal API by parsing the HTML responses.
Each card includes detailed information such as:

- Card name
- Mana cost
- Card type
- Power/Toughness
- Card text
- Set information
- And more...

The data is saved as JSON files with one file per set, named `{SET_CODE}.json`.

## Notes

- The module includes retry logic for handling temporary network issues
- Data is fetched in pages to handle large sets
- The parsing extracts embedded JSON data from Gatherer's HTML responses
