# Double-Faced Card (DFC) Implementation Plan

## Overview

This document outlines the coherent plan for supporting double-faced cards (DFCs) in Scryfall OS, covering both schema design and query-time behavior.

## Schema Design

### Single-Row Approach

**Decision**: Store each DFC as a single row in the database, with merged/unioned data from all faces.

**Rationale**:
- Maintains existing database schema (no migration needed)
- Simplifies queries (no need for joins or grouping)
- Better performance for searches
- Matches Scryfall's behavior of "unioning the sides together before searching" (per issue #101)

### Field Merging Strategy

#### Union Fields (Searchable from ANY face)

These fields are merged so searches can find cards with properties from either face:

1. **Types** (`card_types` JSONB array)
   - Example: "Augmenter Pugilist // Echoing Equation" → `['Creature', 'Sorcery']`
   - Enables: `t:creature t:sorcery` finds the card

2. **Subtypes** (`card_subtypes` JSONB array)
   - Example: "Hound Tamer // Untamed Pup" → `['Human', 'Werewolf']`
   - Enables: `t:human t:werewolf` finds the card

3. **Keywords** (`card_keywords` JSONB object)
   - Example: "Hound Tamer // Untamed Pup" → `{'Trample': True, 'Daybound': True, 'Nightbound': True}`
   - Enables: `keyword:daybound keyword:nightbound` finds the card

4. **Colors** (`card_colors` JSONB object)
   - Merged from face-level color data
   - Example: "Augmenter Pugilist // Echoing Equation" → `{'G': True, 'U': True}`
   - Enables: `(color:g and color:u)` finds the card

5. **Color Identity** (`card_color_identity` JSONB object)
   - Already unioned at Scryfall API level
   - No special handling needed

#### First-Face Priority Fields (Single value only)

These fields use the first face's value to maintain single-row schema:

1. **Power/Toughness/Loyalty** (`creature_power`, `creature_toughness`, `planeswalker_loyalty` integers)
   - Uses first face's numeric value
   - Example: "Hound Tamer // Untamed Pup" (3/3 // 4/4) → stored as 3/3
   - Enables: `power=3` finds the card
   - **Trade-off**: `power=4` will NOT find this card

2. **Mana Cost** (`mana_cost_text`, `mana_cost_jsonb`)
   - Uses first face's mana cost
   - Rationale: Front face is typically the "castable" side

3. **CMC** (`cmc` integer)
   - Already provided by Scryfall at top level
   - Represents the CMC of the front face

#### Combined Fields

1. **Oracle Text** (`oracle_text` text)
   - Combines text from all faces with separator: `\n---\n`
   - Enables full-text search across both faces

2. **Type Line** (`type_line` text)
   - Reconstructed from merged types and subtypes
   - Format: `Type Type — Subtype Subtype`

#### Metadata Fields

1. **Layout** (`card_layout` text)
   - Preserved from Scryfall (e.g., "transform", "modal_dfc")
   - Used for layout-specific tagging

2. **Is Tags** (`card_is_tags` JSONB object)
   - `dfc: True` for all double-faced cards
   - Layout-specific tags: `transform: True`, `modal_dfc: True`, etc.

3. **Raw Card Blob** (`raw_card_blob` JSONB)
   - Preserves complete original Scryfall data including all faces
   - Allows future enhancements without data loss

## Query-Time Behavior

### Search Operations

#### Type/Subtype Searches
```
t:creature t:sorcery
```
- Searches `card_types` JSONB array
- Finds cards with EITHER type (unioned)
- Example: Finds "Augmenter Pugilist // Echoing Equation"

#### Keyword Searches
```
is:dfc keyword:flying t:human t:horror
```
- Searches `card_keywords` JSONB object
- Finds cards with keywords from ANY face
- Example: Finds DFCs where one face is Human/Horror with flying

#### Color Searches
```
pugilist (color:g and color:u)
```
- Searches `card_colors` JSONB object using containment
- Finds cards with colors from EITHER face
- Example: Finds cards with G on one face and U on another

#### Power/Toughness Searches
```
power=3
```
- Searches `creature_power` integer column
- Uses first face's value only
- Example: "Hound Tamer // Untamed Pup" (3/3 // 4/4) matches `power=3` but NOT `power=4`

#### DFC Filtering
```
is:dfc
is:transform
is:modal_dfc
```
- Searches `card_is_tags` JSONB object
- Filters to only DFC cards or specific layouts

### Index Usage

Existing indexes work without modification:
- `card_types` → GIN index for type searches
- `card_keywords` → GIN index for keyword searches
- `card_colors` → GIN index for color searches
- `creature_power`, `creature_toughness` → B-tree indexes for numeric comparisons
- `card_is_tags` → GIN index for tag searches

## Implementation Details

### Card Processing Flow

1. **Input**: Scryfall card JSON with `card_faces` array
2. **Merge Faces**: Call `merge_dfc_faces()` function
   - Extract data from each face
   - Union types, subtypes, keywords, colors
   - Take first face's power/toughness/loyalty
   - Reconstruct merged type_line
   - Combine oracle_text
3. **Process as Normal**: Continue with standard card processing
4. **Add Tags**: Set `is:dfc` and layout-specific tags
5. **Store**: Insert single row into database

### Code Structure

```python
def merge_dfc_faces(card: dict) -> dict:
    """Merge all faces into single searchable representation."""
    # Extract and union data from faces
    # Set top-level fields for processing
    return card

def preprocess_card(card: dict) -> dict:
    """Process card including DFC handling."""
    if "card_faces" in card:
        card = merge_dfc_faces(card)
        has_card_faces = True
    
    # Standard processing...
    
    if has_card_faces:
        card["card_is_tags"]["dfc"] = True
        card["card_is_tags"][layout] = True
    
    return card
```

## Design Trade-offs

### Chosen: Single-Row with Face Merging

**Pros**:
- ✅ No schema migration needed
- ✅ Simple queries (no joins)
- ✅ Better performance
- ✅ Matches Scryfall behavior for type/color searches
- ✅ Backward compatible

**Cons**:
- ❌ Power/toughness only searchable from first face
- ❌ Cannot search for "front face only" vs "back face only"

### Alternative: Multi-Row Approach

**Not chosen**: Store one row per face, deduplicate at query time

**Why not**:
- ❌ Requires schema changes
- ❌ More complex queries (need DISTINCT or GROUP BY)
- ❌ Performance concerns with large datasets
- ❌ Complicates card uniqueness constraints

### Alternative: Array Fields for Multi-Values

**Not chosen**: Store power/toughness as arrays

**Why not**:
- ❌ Requires schema changes
- ❌ Complicates numeric comparisons
- ❌ Would need custom operators or functions
- ❌ Not needed for most use cases

## Future Enhancements

If power/toughness searchability from all faces becomes critical:

1. **Option A**: Add separate array fields
   - `creature_powers` JSONB array
   - `creature_toughnesses` JSONB array
   - Keep existing integer fields for common case
   - Add special operators for array searches

2. **Option B**: Store face data in JSONB
   - `card_face_data` JSONB column
   - Index specific paths with GIN
   - Query with JSON path operators

3. **Option C**: Multi-row with view
   - Store multiple rows internally
   - Create materialized view with merged data
   - Query view for normal searches
   - Query raw tables for face-specific searches

## Testing Strategy

### Unit Tests
- ✅ Transform DFCs (werewolves)
- ✅ Modal DFCs (MDFCs)
- ✅ Type/subtype merging
- ✅ Keyword merging
- ✅ Color merging
- ✅ Tag generation

### Integration Tests
- Search queries with real Scryfall data
- Verify all search patterns from issue #101 work
- Performance testing with large datasets

### Edge Cases
- Single-faced cards (no change)
- DFCs with no power/toughness
- DFCs with >2 faces (if they exist)
- Missing face data

## Compatibility with Scryfall

Our implementation matches Scryfall's behavior:

1. ✅ **Type searches**: Union types from all faces
2. ✅ **Color searches**: Union colors from all faces
3. ✅ **Keyword searches**: Union keywords from all faces
4. ✅ **Name storage**: Store "Front // Back" format
5. ⚠️ **Power/toughness**: Partial match (first face only)

The power/toughness limitation is documented and acceptable given the schema constraints.

## Conclusion

This implementation provides a coherent, well-tested approach to DFC support that:
- Maintains schema simplicity
- Enables most search patterns from the issue
- Preserves performance characteristics
- Allows future enhancements through `raw_card_blob`

The trade-off of first-face-only power/toughness is acceptable and documented.
