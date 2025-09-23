# Card Data Export/Import

This document describes the card data export and import functionality that allows you to backup and restore the three main database tables.

## Overview

The export/import functionality provides a way to:
- Export card data to CSV files for backup purposes
- Import previously exported data to restore database state  
- Transfer data between instances of the application

## Tables Included

The following tables are included in export/import operations:

- **`magic.cards`** - All card data including JSONB fields
- **`magic.tags`** - Tag definitions
- **`magic.tag_relationships`** - Tag hierarchy relationships

## API Endpoints

### Export Data

**Endpoint:** `GET /export_card_data`

Exports all card data to timestamped CSV files in `/data/api/exports/{timestamp}/` directory.

**Example:**
```bash
curl "http://localhost:8080/export_card_data"
```

**Response:**
```json
{
  "status": "success",
  "export_directory": "/data/api/exports/20241001_143052",
  "timestamp": "20241001_143052", 
  "results": {
    "cards": {"file": "/data/api/exports/20241001_143052/cards.csv", "count": 25847},
    "tags": {"file": "/data/api/exports/20241001_143052/tags.csv", "count": 142},
    "tag_relationships": {"file": "/data/api/exports/20241001_143052/tag_relationships.csv", "count": 85}
  },
  "message": "Successfully exported 25847 cards, 142 tags, and 85 tag relationships"
}
```

### Import Data

**Endpoint:** `GET /import_card_data[?timestamp=YYYYMMDD_HHMMSS]`

Imports card data from CSV files, truncating existing data first.

**Parameters:**
- `timestamp` (optional) - Specific export timestamp to import. If not provided, uses the most recent export.

**Examples:**
```bash
# Import latest export
curl "http://localhost:8080/import_card_data"

# Import specific timestamp
curl "http://localhost:8080/import_card_data?timestamp=20241001_143052"
```

**Response:**
```json
{
  "status": "success",
  "timestamp": "20241001_143052",
  "import_directory": "/data/api/exports/20241001_143052",
  "results": {
    "tags": 142,
    "tag_relationships": 85, 
    "cards": 25847
  },
  "message": "Successfully imported 25847 cards, 142 tags, and 85 tag relationships"
}
```

## File Structure

Each export creates a timestamped directory containing three CSV files:

```
/data/api/exports/
├── 20241001_143052/
│   ├── cards.csv
│   ├── tags.csv
│   └── tag_relationships.csv
├── 20241001_120000/
│   ├── cards.csv
│   ├── tags.csv
│   └── tag_relationships.csv
└── ...
```

### File Formats

**cards.csv** - Contains all card data with the following columns:
- `card_name`, `cmc`, `mana_cost_text`, `mana_cost_jsonb`, `raw_card_blob`
- `card_types`, `card_subtypes`, `card_colors`, `card_color_identity`
- `card_keywords`, `oracle_text`, `edhrec_rank`
- `creature_power`, `creature_power_text`, `creature_toughness`, `creature_toughness_text`
- `card_oracle_tags`

**tags.csv** - Contains tag definitions:
- `tag`

**tag_relationships.csv** - Contains tag hierarchy:
- `child_tag`, `parent_tag`

## Important Notes

### Data Safety
- **Import truncates all existing data** in the three tables before importing
- Always verify you have a recent export before importing
- The import operation is transactional - it will rollback on errors

### JSONB Handling
- JSONB columns are converted to text for CSV compatibility during export
- They are automatically converted back to JSONB during import

### Docker Volumes
- The `/data/api/exports/` directory is mounted as a Docker volume
- Exports persist between container restarts
- You can access exported files from the host system

### Performance
- Export processes data in batches for memory efficiency
- Import uses PostgreSQL COPY for fast bulk loading
- Large datasets may take several minutes to export/import

## Error Handling

Common error scenarios and responses:

**No exports directory:**
```json
{
  "status": "error",
  "message": "No exports directory found at /data/api/exports"
}
```

**Missing timestamp:**
```json
{
  "status": "error", 
  "message": "Export directory for timestamp 20241001_999999 not found"
}
```

**Missing files:**
```json
{
  "status": "error",
  "message": "Missing required files: tags.csv, tag_relationships.csv"
}
```

## Use Cases

### Backup and Restore
```bash
# Create backup
curl "http://localhost:8080/export_card_data"

# Later, restore from backup
curl "http://localhost:8080/import_card_data?timestamp=20241001_143052"
```

### Data Migration
```bash
# On source instance
curl "http://source.example.com:8080/export_card_data"

# Copy files to destination instance's /data/api/exports/ directory

# On destination instance  
curl "http://destination.example.com:8080/import_card_data"
```

### Development Reset
```bash
# Export current state
curl "http://localhost:8080/export_card_data"

# Make changes, test, then restore
curl "http://localhost:8080/import_card_data"
```