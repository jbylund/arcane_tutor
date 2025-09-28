# API Design for Multiple Printings Support

This document outlines the API changes needed to support multiple card printings and unique search modes.

## Current API Limitations

The current `/search` endpoint has these limitations:
1. Returns only one printing per card name
2. No way to specify which printing to return
3. No access to set information, alternate arts, or promotional versions
4. Response format assumes single printing per card

## New API Design

### Search Endpoint Enhancement

#### New Parameter: `unique`

Add support for different uniqueness modes:

```
GET /search?q={query}&unique={mode}
```

**Supported Values**:
- `cards` (default) - One result per unique card (Oracle ID)  
- `art` - One result per unique artwork (illustration_id)
- `prints` - All matching printings

**Examples**:
```
GET /search?q=lightning%20bolt&unique=cards     # Default behavior
GET /search?q=lightning%20bolt&unique=art       # Unique artworks
GET /search?q=lightning%20bolt&unique=prints    # All printings
```

#### Backwards Compatibility

- Default `unique=cards` maintains current behavior
- Existing API clients continue to work without changes
- Response format enhanced but backwards compatible

### Enhanced Response Format

#### Core Response Structure

```json
{
  "cards": [
    {
      // Core card data (from oracle_cards)
      "oracle_id": "6fe14082-5544-5c7c-9b80-1cd5c33a02d3",
      "name": "Lightning Bolt", 
      "mana_cost": "{R}",
      "cmc": 1,
      "type_line": "Instant",
      "oracle_text": "Lightning Bolt deals 3 damage to any target.",
      "colors": ["R"],
      "color_identity": ["R"],
      "keywords": [],
      
      // Printing-specific data (from card_printings)
      "printing_id": "c1f55a84-b0a1-4b87-991a-eb1a5ba3b45e",
      "set": "lea",
      "set_name": "Limited Edition Alpha", 
      "collector_number": "161",
      "rarity": "common",
      "artist": "Christopher Rush",
      "flavor_text": null,
      "illustration_id": "94fe723d-64d8-4fa6-8f64-5d12c0a0e91b",
      "released_at": "1993-08-05",
      
      // Visual attributes
      "border_color": "black",
      "frame": "1993",
      "layout": "normal",
      "image_status": "highres_scan",
      "image_uris": {
        "small": "https://cards.scryfall.io/small/...",
        "normal": "https://cards.scryfall.io/normal/...",
        "large": "https://cards.scryfall.io/large/..."
      },
      
      // Market data
      "prices": {
        "usd": "245.00",
        "usd_foil": null,
        "eur": "220.00",
        "eur_foil": null,
        "tix": null
      },
      
      // Format legality
      "legalities": {
        "standard": "not_legal",
        "future": "not_legal", 
        "historic": "not_legal",
        "pioneer": "not_legal",
        "modern": "legal",
        "legacy": "legal",
        "pauper": "legal",
        "vintage": "legal",
        "penny": "not_legal",
        "commander": "legal"
      }
    }
  ],
  
  // Response metadata
  "unique_mode": "cards",
  "total_cards": 1,
  "total_printings": 847,
  "has_more": false,
  "next_page": null,
  
  // Query debugging (existing)
  "query": "lightning bolt",
  "compiled": "SELECT ...",
  "params": {...}
}
```

#### Response Field Details

**Core Card Fields** (from `oracle_cards`):
- `oracle_id` - Unique identifier for the card concept
- `name` - Canonical card name
- `mana_cost` - Mana cost in Scryfall format  
- `cmc` - Converted mana cost
- `type_line` - Full type line
- `oracle_text` - Current Oracle text
- `colors` - Array of color codes
- `color_identity` - Array of color identity codes
- `keywords` - Array of keyword abilities
- `power`/`toughness` - For creatures (strings to handle */*)
- `loyalty` - For planeswalkers

**Printing Fields** (from `card_printings`):
- `printing_id` - Unique identifier for this specific printing
- `set`/`set_name` - Set information
- `collector_number` - Collector number in set
- `rarity` - Rarity for this printing
- `artist` - Artist name
- `flavor_text` - Flavor text (if any)
- `illustration_id` - Groups cards with same artwork
- `released_at` - Release date
- `border_color` - Border color
- `frame` - Frame type/year
- `layout` - Card layout
- `image_status` - Image scan quality
- `image_uris` - Image URLs at different sizes
- `prices` - Current market prices
- `legalities` - Format legality

**Response Metadata**:
- `unique_mode` - Which uniqueness mode was used
- `total_cards` - Total unique results (respecting unique mode)
- `total_printings` - Total printings that match (if different)

### Query Generation Logic

The API will generate different SQL queries based on the `unique` parameter:

#### `unique=cards` (Default)

```sql
SELECT DISTINCT ON (oc.oracle_id)
    oc.oracle_id,
    oc.name,
    oc.mana_cost,
    oc.cmc,
    oc.type_line,
    oc.oracle_text,
    oc.colors,
    oc.color_identity,
    oc.keywords,
    oc.power,
    oc.toughness,
    oc.loyalty,
    
    cp.id as printing_id,
    cp.set_code as set,
    cp.set_name,
    cp.collector_number,
    cp.rarity,
    cp.artist,
    cp.flavor_text,
    cp.illustration_id,
    cp.released_at,
    cp.border_color,
    cp.frame,
    cp.layout,
    cp.image_status,
    cp.image_uris,
    cp.prices,
    cp.legalities
    
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE {search_conditions}
ORDER BY oc.oracle_id, cp.released_at DESC, cp.set_code
LIMIT {limit}
```

#### `unique=art`

```sql  
SELECT DISTINCT ON (cp.illustration_id)
    oc.oracle_id,
    oc.name,
    -- ... same fields as above
    
FROM magic.oracle_cards oc  
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id
WHERE {search_conditions} 
    AND cp.illustration_id IS NOT NULL
ORDER BY cp.illustration_id, cp.released_at DESC, cp.set_code
LIMIT {limit}
```

#### `unique=prints`

```sql
SELECT 
    oc.oracle_id,
    oc.name,
    -- ... same fields as above
    
FROM magic.oracle_cards oc
JOIN magic.card_printings cp ON oc.oracle_id = cp.oracle_id  
WHERE {search_conditions}
ORDER BY oc.name, cp.released_at DESC, cp.set_code
LIMIT {limit}
```

### Search Syntax Extensions

#### Set-Specific Search

New search syntax to find specific printings:

```
GET /search?q=set:lea lightning bolt    # Lightning Bolt from Alpha
GET /search?q=set:beta -set:lea bolt    # Beta but not Alpha  
GET /search?q=artist:"christopher rush" # All cards by specific artist
```

#### Printing-Specific Attributes

Support for printing-specific searches:

```
GET /search?q=rarity:mythic frame:2015          # Mythics with modern frame
GET /search?q=border:black -border:white        # Black border only
GET /search?q=collector:1-100 set:dom           # Dominaria cards 1-100
```

### Pagination Enhancement

#### Cursor-Based Pagination

For `unique=prints`, implement cursor-based pagination for better performance:

```json
{
  "has_more": true,
  "next_page": "https://api.scryfall.os/search?q=bolt&unique=prints&after=cursor123",
  "total_cards": 50,
  "total_printings": 2847
}
```

### Performance Considerations

#### Caching Strategy

- Cache popular queries for each unique mode separately
- Key format: `{query_hash}:{unique_mode}:{limit}:{page}`
- TTL: 60 seconds (same as current implementation)

#### Query Optimization

- Use DISTINCT ON for better performance than GROUP BY
- Leverage existing indexes for fast lookups
- Limit subquery complexity for printing joins

#### Response Size Management

- `unique=prints` can return many more results
- Implement reasonable default limits (100 for cards/art, 50 for prints)
- Provide clear pagination for large result sets

### Error Handling

#### New Error Cases

```json
{
  "error": "invalid_parameter",
  "message": "Invalid unique mode 'invalid'. Must be one of: cards, art, prints",
  "code": 400
}
```

#### Enhanced Validation

- Validate `unique` parameter values
- Provide helpful error messages for malformed queries
- Maintain existing error handling for backwards compatibility

### Documentation Updates

#### API Reference

Update existing API documentation with:
- New `unique` parameter description
- Enhanced response format examples
- New search syntax for sets and printing attributes
- Performance considerations for different modes

#### Migration Guide

Provide migration guide for API consumers:
- How to maintain existing behavior
- Benefits of new unique modes
- Examples of common use cases

## Implementation Strategy

### Phase 1: Core Infrastructure
- Add `unique` parameter parsing
- Implement query generation logic
- Create response transformation layer

### Phase 2: Enhanced Features
- Add printing-specific search syntax
- Implement cursor-based pagination
- Add caching for new query patterns

### Phase 3: Optimization
- Performance tuning based on usage patterns
- Advanced caching strategies
- Query plan optimization

This design ensures backwards compatibility while providing powerful new functionality for accessing multiple card printings and different uniqueness modes.