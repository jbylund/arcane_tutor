# Devotion and Date/Year Search Implementation

## Overview

Added comprehensive support for devotion counting and release date filtering, addressing two key features from the Scryfall search syntax.

## Features Implemented

### 1. Devotion Search

Devotion counts the number of mana symbols of a specific color in a card's mana cost.

**Syntax:**
- `devotionw>=5` - Cards with 5 or more white mana symbols
- `devotionu>=3` - Cards with 3 or more blue mana symbols
- `devotionb>=2` - Cards with 2 or more black mana symbols
- `devotionr>=4` - Cards with 4 or more red mana symbols
- `devotiong>=3` - Cards with 3 or more green mana symbols
- `devotionc>=1` - Cards with 1 or more colorless mana symbols

**Examples:**
- `devotionw>=5` - Find cards with 5+ white mana symbols (e.g., Devotion decks)
- `devotionu>=3 AND type:creature` - Blue creatures with high devotion
- `devotionr>2 AND cmc<=4` - Red cards with significant red devotion at low CMC

**Technical Implementation:**
- Uses `jsonb_array_length()` to count mana symbols in `mana_cost_jsonb`
- Supports all comparison operators: `=`, `!=`, `<`, `<=`, `>`, `>=`
- Efficient JSONB-based counting without full table scans
- Color validation ensures only valid color codes (W, U, B, R, G, C)

### 2. Release Date Search

Filter cards by their release date using either full dates or just the year.

**Date Syntax:**
- `date=2020-01-01` - Cards released on a specific date
- `date>=2020-01-01` - Cards released on or after a date
- `date>2020-06-15` - Cards released after a date
- `date<=2020-12-31` - Cards released on or before a date
- `date<2021-01-01` - Cards released before a date

**Year Syntax:**
- `year=2020` - Cards released in 2020
- `year>=2020` - Cards released in 2020 or later
- `year>2019` - Cards released after 2019
- `year<=2020` - Cards released in 2020 or earlier
- `year<2021` - Cards released before 2021

**Examples:**
- `year=2023 AND type:creature` - All creatures from 2023
- `date>=2020-01-01 AND date<2021-01-01` - Cards from 2020
- `year>=2020 AND devotionw>=3` - Recent cards with high white devotion
- `date>=2020-01-01 AND cmc<=3` - Recent low-cost cards

**Technical Implementation:**
- Date queries use the `released_at` column directly
- Year queries use `EXTRACT(YEAR FROM released_at)` for efficient filtering
- Supports all comparison operators for both date and year
- Date values are validated and handled as strings or numerics as appropriate

## Database Schema

Both features utilize existing database columns:

1. **Devotion**: Uses `mana_cost_jsonb` column
   - Structure: `{"W": [1, 2, 3], "U": [1], ...}`
   - Array length indicates devotion count for each color

2. **Date/Year**: Uses `released_at` column
   - Type: `date NOT NULL`
   - Indexed for efficient date range queries
   - Year extraction via PostgreSQL's `EXTRACT()` function

## Testing

Comprehensive test coverage added:

### Parsing Tests (23 tests)
- Devotion parsing for all colors (W, U, B, R, G, C)
- All comparison operators for devotion
- Date parsing with ISO 8601 format
- Year parsing with numeric values
- Combined queries with other search criteria

### SQL Generation Tests (26 tests)
- Correct JSONB array length counting for devotion
- Proper year extraction from `released_at`
- Date comparison SQL generation
- Operator conversion (`:` to `=`)
- Complex queries combining devotion/date with other attributes

### Total Test Count
- **441 tests** in the complete parsing test suite (up from 376)
- All tests passing with comprehensive coverage
- No regressions in existing functionality

## Performance Considerations

### Devotion Queries
- **Efficient**: Uses JSONB array length operation
- **Index-Friendly**: JSONB GIN indexes support containment queries
- **Fast Lookup**: O(1) array length calculation per card

### Date/Year Queries
- **Indexed**: `released_at` column supports B-tree indexing
- **Range Queries**: Efficient date range filtering with PostgreSQL
- **Year Extraction**: Functional index possible for `EXTRACT(YEAR)` if needed

## API Examples

### Devotion Queries
```
GET /search?q=devotionw>=5
GET /search?q=devotionu>=3+AND+type:creature
GET /search?q=devotionr>2+AND+cmc<=4
```

### Date Queries
```
GET /search?q=year=2023
GET /search?q=date>=2020-01-01
GET /search?q=year>=2020+AND+type:legendary
```

### Combined Queries
```
GET /search?q=devotionw>=3+AND+year>=2020
GET /search?q=date>=2020-01-01+AND+devotionu>=2+AND+cmc<=4
```

## Migration Notes

No database migration required - both features use existing columns:
- `mana_cost_jsonb` (already populated)
- `released_at` (already populated and indexed)

## Future Enhancements

Potential improvements for consideration:
1. Support for devotion with hybrid mana (e.g., W/U counts for both)
2. Total devotion across all colors
3. Devotion to multicolor combinations
4. Date ranges with more natural language (e.g., "last year", "this month")
5. Season-based date filtering (e.g., "spring 2023")

## Documentation Updates

- Updated README.md with devotion and date/year in "Recently Completed" section
- Moved features from "Missing Functionality" grid
- Updated test count to 441 tests
- Added devotion and date to "Fully Supported Features" table
- Updated API success rate description

## References

- [Scryfall Mana Syntax](https://scryfall.com/docs/syntax#mana)
- [Scryfall Year Syntax](https://scryfall.com/docs/syntax#year)
- Issue: "Add support for devotion and released at search"
