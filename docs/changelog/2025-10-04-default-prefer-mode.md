# Default Prefer Mode Implementation

## Overview

This document describes the implementation of the default prefer mode for card selection in Scryfall OS. The prefer mode determines which printing of a card is selected when multiple printings exist.

## Implementation Date

2025-10-04

## Problem Statement

When searching for cards with `unique=card` or `unique=artwork`, the system needs to decide which printing to prefer among the available options. Previously, the default behavior used `edhrec_rank` as a fallback, but this didn't account for important card attributes like border color, frame version, artwork popularity, or rarity.

## Solution

A new `prefer_score` column was added to the `magic.cards` table, calculated based on multiple attributes with weighted scoring:

### Scoring Components

1. **Border Color** (0-100 points)
   - Black border: 100 points (most preferred)
   - White border: 20 points
   - Borderless: 20 points
   - Silver border: 0 points (least preferred)
   - Gold border: 0 points (least preferred)

2. **Frame Version** (0-100 points)
   - 2015 frame: 100 points (most preferred, modern frame)
   - 2003 frame: 50 points (classic modern frame)
   - 1997 frame: 25 points (older frame)
   - 1993 frame: 10 points (original frame)
   - Other frames: 0 points

3. **Artwork Popularity** (5-100 points)
   - Based on number of printings with the same `illustration_id`
   - 40+ printings: 100 points (iconic artwork)
   - 20+ printings: 75 points
   - 10+ printings: 50 points
   - 5+ printings: 35 points
   - 3+ printings: 25 points
   - 2+ printings: 15 points
   - 1 printing: 5 points (minimum for unique artwork)

4. **Rarity** (0-100 points)
   - Common: 100 points (most preferred - most accessible)
   - Uncommon: 25 points
   - Rare: 10 points
   - Mythic: 5 points (least preferred among regular rarities)
   - Special: 0 points
   - Bonus: 0 points

   Note: The scoring is inversely proportional to rarity, preferring more common printings that are easier to obtain.

5. **Extended Art** (0-100 points)
   - Has Extendedart frame effect: 100 points
   - No extended art: 0 points

### Total Score Range

- **Maximum possible score**: 500 points
  - Black border (100) + 2015 frame (100) + 40+ printings (100) + Common (100) + Extended art (100)
  
- **Minimum possible score**: 5 points
  - Silver/gold border (0) + Other frame (0) + 1 printing (5) + Special/bonus rarity (0) + No extended art (0)

## Database Changes

### Migration: `2025-10-04-01-default-prefer-score.sql`

1. **New column**: `prefer_score` (real type) added to `magic.cards` table
2. **New function**: `magic.calculate_prefer_score()` - PostgreSQL function to calculate the score
3. **Index**: `idx_cards_prefer_score` - B-tree index for efficient sorting (DESC NULLS LAST)
4. **Trigger**: `trg_update_prefer_score` - Automatically updates the score on INSERT or UPDATE
5. **Initial population**: All existing cards have their `prefer_score` calculated during migration

## API Changes

### `api/api_resource.py`

Updated the `prefer_mapping` dictionary in the `_search()` method:

```python
PreferOrder.DEFAULT: ("prefer_score", "DESC"),
```

The default prefer order now uses `prefer_score DESC` instead of `edhrec_rank ASC`, sorting cards by their calculated preference score in descending order (higher scores first).

## Testing

### Test Files

1. **`api/tests/test_prefer_order.py`**
   - Added test to verify `prefer_score` is used in SQL queries when `PreferOrder.DEFAULT` is selected

2. **`api/tests/test_prefer_score_calculation.py`** (new)
   - Documents and validates scoring logic for each component
   - Tests minimum and maximum possible scores
   - Provides comprehensive documentation of expected behavior

### Test Results

- All 631 tests pass (10 new tests added)
- Linting passes with no issues
- No regressions introduced

## Usage Examples

### Default Search (uses prefer_score)

```python
# Search with default prefer mode
result = api_resource.search(
    query="name:lightning",
    unique="card",
    prefer=PreferOrder.DEFAULT
)
```

The system will prefer:
- Black-bordered cards over white-bordered
- 2015 frame over older frames
- More popular artwork (by printing count)
- Common printings over rare
- Extended art versions

### Other Prefer Modes

The existing prefer modes are unchanged:
- `PreferOrder.OLDEST` - Prefer oldest printing by release date
- `PreferOrder.NEWEST` - Prefer newest printing by release date
- `PreferOrder.USD_LOW` - Prefer lowest USD price
- `PreferOrder.USD_HIGH` - Prefer highest USD price
- `PreferOrder.PROMO` - Prefer promotional printings (currently uses edhrec_rank)

## Performance Considerations

1. **Index**: The B-tree index on `prefer_score` ensures efficient sorting
2. **Trigger**: The automatic trigger adds minimal overhead on INSERT/UPDATE
3. **Calculation**: The `calculate_prefer_score()` function is marked as IMMUTABLE for optimization
4. **Caching**: The `_search()` method uses TTL cache (60s) to reduce repeated calculations

## Future Improvements

Potential enhancements for future iterations:

1. **Promo scoring**: Add specific logic for promotional printings
2. **Set preference**: Add scoring based on set popularity or significance
3. **Foil preference**: Consider foil vs non-foil printings
4. **Price-based adjustments**: Factor in price data for determining "canonical" printings
5. **User customization**: Allow users to adjust scoring weights via API parameters
6. **Set-specific overrides**: Manual overrides for specific cards that should prefer certain printings

## References

- Issue: "Implement Default Prefer Mode"
- Migration file: `api/db/2025-10-04-01-default-prefer-score.sql`
- Test files: `api/tests/test_prefer_order.py`, `api/tests/test_prefer_score_calculation.py`
