# Double-Faced Cards (DFC) Support

This document describes how Scryfall OS handles Double-Faced Cards (DFCs), including transforming cards, modal double-faced cards (MDFCs), and other cards with multiple faces.

## Overview

Double-Faced Cards are a card type introduced in Innistrad that have two distinct faces with different attributes. Examples include:

- **Transforming DFCs**: Cards like "Hound Tamer // Untamed Pup" that transform based on game conditions
- **Modal DFCs**: Cards like "Augmenter Pugilist // Echoing Equation" where you can cast either face
- **Adventure Cards**: Cards with an Adventure on one side (not currently supported)
- **Split Cards**: Cards like "Fire // Ice" (not currently supported)

## Data Model

### Card Storage

DFC cards are stored with one database row per face per printing:

```sql
-- Example: Hound Tamer // Untamed Pup has 3 printings
-- This results in 6 rows in the database (3 printings × 2 faces)

SELECT 
    card_name, 
    face_name, 
    face_idx, 
    creature_power, 
    creature_toughness 
FROM magic.cards 
WHERE card_name = 'Hound Tamer // Untamed Pup'
ORDER BY face_idx;

-- Result:
-- card_name                    | face_name    | face_idx | power | toughness
-- ----------------------------|--------------|----------|-------|----------
-- Hound Tamer // Untamed Pup | Hound Tamer  | 1        | 3     | 3
-- Hound Tamer // Untamed Pup | Hound Tamer  | 1        | 3     | 3
-- Hound Tamer // Untamed Pup | Hound Tamer  | 1        | 3     | 3
-- Hound Tamer // Untamed Pup | Untamed Pup  | 2        | 4     | 4
-- Hound Tamer // Untamed Pup | Untamed Pup  | 2        | 4     | 4
-- Hound Tamer // Untamed Pup | Untamed Pup  | 2        | 4     | 4
```

### Key Fields

- **`card_name`**: The full card name with both faces separated by " // " (e.g., "Hound Tamer // Untamed Pup")
- **`face_name`**: The name of the specific face (e.g., "Hound Tamer" or "Untamed Pup")
- **`face_idx`**: Integer indicating which face (1 = front, 2 = back)
- **Face-specific attributes**: power, toughness, mana cost, types, colors, oracle text, etc.

### Card-Level vs Face-Level Attributes

Some attributes are stored at the face level, while others are aggregated across all faces:

**Face-Level Attributes** (different for each face):
- `face_name` - Individual face name
- `creature_power`, `creature_toughness` - Combat stats
- `card_types`, `card_subtypes` - Type line components
- `card_colors` - Colors of this face
- `mana_cost_text`, `mana_cost_jsonb` - Mana cost
- `oracle_text` - Rules text

**Card-Level Attributes** (shared across faces):
- `card_name` - Full card name
- `card_color_identity` - Union of all face colors
- `card_keywords` - Union of all keywords from both faces
- `edhrec_rank` - Commander popularity ranking

**Print-Level Attributes** (shared within a printing):
- `scryfall_id` - Unique ID for this printing
- `card_set_code` - Set code
- `collector_number` - Collector number
- `card_rarity_text`, `card_rarity_int` - Rarity
- `price_usd`, `price_eur`, `price_tix` - Pricing
- `image_location_uuid` - Image identifier
- `illustration_id` - Art identifier

## Search Behavior

### Union Semantics

When searching for DFC cards, Scryfall OS follows Scryfall's union semantics:
- A search matches a card if **any face** matches the criteria
- Results are de-duplicated to show one result per card

### Examples

#### Power/Toughness Searches

```
power=3 name:hound
→ Finds "Hound Tamer // Untamed Pup" (front face has power 3)

power=4 name:hound
→ Also finds "Hound Tamer // Untamed Pup" (back face has power 4)

power=3 power=4 name:hound
→ Also finds the card (one face has power 3, another has power 4)
```

#### Type Searches

```
t:creature t:sorcery name:augmenter
→ Finds "Augmenter Pugilist // Echoing Equation"
   (front is Creature, back is Sorcery)

t:human name:hound
→ Finds "Hound Tamer // Untamed Pup" 
   (front is Human Werewolf, back is just Werewolf)

t:horror is:dfc
→ Finds all DFCs where at least one face is a Horror
```

#### Name Searches

```
name:"Hound Tamer"
→ Finds "Hound Tamer // Untamed Pup"

name:"Untamed Pup"  
→ Also finds "Hound Tamer // Untamed Pup"

name:"Hound Tamer // Untamed Pup"
→ Finds the card using full name
```

#### Color Searches

```
color:g name:hound
→ Finds "Hound Tamer // Untamed Pup" (both faces are green)

# For MDFCs with different colors:
color:g name:augmenter
→ Finds "Augmenter Pugilist // Echoing Equation" (front face is green)

color:u name:augmenter  
→ Also finds it (back face is blue)

color:gu name:augmenter
→ Requires BOTH colors to be present across all faces
→ Will NOT find it with exact match (needs separate searches)

(color:g and color:u) name:augmenter
→ Finds it (checks each color separately)
```

## Implementation Details

### Card Processing

The `preprocess_card()` function in `api/card_processing.py` handles DFC cards recursively:

1. Detects `card_faces` array in Scryfall JSON
2. Extracts the full card name before processing faces
3. Recursively processes each face, merging card-level and face-level data
4. Assigns `face_idx` (1 for front, 2 for back)
5. Returns a list of processed face dictionaries

```python
# Example processing flow:
card = {
    "name": "Hound Tamer // Untamed Pup",
    "card_faces": [
        {"name": "Hound Tamer", "power": "3", ...},
        {"name": "Untamed Pup", "power": "4", ...}
    ]
}

result = preprocess_card(card)
# Returns: [
#   {face_idx: 1, card_name: "Hound Tamer // Untamed Pup", face_name: "Hound Tamer", ...},
#   {face_idx: 2, card_name: "Hound Tamer // Untamed Pup", face_name: "Untamed Pup", ...}
# ]
```

### Query Processing

Search queries use `DISTINCT ON (card_name)` to return one result per card:

```sql
WITH distinct_cards AS (
    SELECT DISTINCT ON (card_name)
        card_name,
        type_line,
        oracle_text,
        ...
    FROM magic.cards AS card
    WHERE 
        (card.creature_power = 3 OR card.creature_power = 4) AND
        card.card_name ILIKE '%hound%'
    ORDER BY
        card_name,
        prefer_score DESC
)
SELECT * FROM distinct_cards;
```

This ensures that:
1. Both faces are searched
2. One result is returned per card
3. The "preferred" printing is chosen based on `prefer_score`

## Testing

### Unit Tests

DFC processing is tested in `api/tests/test_card_processing.py`:

- `test_preprocess_card_double_faced_card`: Basic DFC with different power/toughness
- `test_preprocess_card_double_faced_card_with_different_attributes`: DFC with different subtypes
- `test_preprocess_card_modal_double_faced_card`: MDFC with different card types

### Integration Tests

End-to-end DFC support is tested in `api/tests/test_integration_testcontainers.py`:

- `test_double_faced_card_import_and_search`: Imports real DFC and tests various search patterns

## Importing DFC Cards

DFC cards can be imported using the standard import methods:

```python
# Import by name
api_resource.import_card_by_name(card_name="Hound Tamer // Untamed Pup")

# Import by search
api_resource.import_cards_by_search(query="is:dfc set:mid")
```

The `find_missing_cards.py` script has been updated to include DFC cards (the `-is:dfc` filter has been removed).

## Database Schema

The main cards table supports DFCs with these key columns:

```sql
CREATE TABLE magic.cards (
    -- Card identification
    card_name TEXT NOT NULL,           -- Full name: "Front // Back"
    face_name TEXT NOT NULL,           -- Individual face name
    face_idx INTEGER NOT NULL,         -- 1 or 2
    
    -- Face-specific attributes
    creature_power INTEGER,
    creature_toughness INTEGER,
    card_types JSONB NOT NULL,
    card_subtypes JSONB DEFAULT '[]'::jsonb NOT NULL,
    card_colors JSONB NOT NULL,
    mana_cost_text TEXT,
    oracle_text TEXT,
    
    -- Card-level attributes
    card_color_identity JSONB NOT NULL,
    card_keywords JSONB NOT NULL,
    edhrec_rank INTEGER,
    
    -- Print-specific attributes
    scryfall_id UUID NOT NULL,
    card_set_code TEXT,
    collector_number TEXT,
    ...
);
```

## Migration

The DFC migration (`api/db/2025-10-12-dfc.sql`) creates views for working with DFC data:

- `s_dfc.card_faces`: Distinct faces with their attributes
- `s_dfc.cards`: Card-level view grouping faces together
- `s_dfc.face_prints`: Print-specific data per face
- `s_dfc.prints`: Print-level view with front and back faces
- `s_dfc.cards_with_prints`: Complete view joining cards and prints

These views are useful for analytics but searches use the main `magic.cards` table directly.

## Limitations and Future Work

### Currently Supported
✅ Transforming DFCs (e.g., Werewolves)
✅ Modal DFCs (e.g., MDFCs from Zendikar Rising)
✅ Search across all faces
✅ Proper de-duplication in results
✅ Import from Scryfall API

### Not Yet Supported
❌ Adventure cards (excluded by `-is:adventure` filter)
❌ Split cards (excluded by `-is:split` filter)
❌ Displaying both faces in UI
❌ Special handling for transformed state in search

## References

- Scryfall DFC Documentation: https://scryfall.com/docs/api/layouts
- Sample DFC Data: [`docs/sample_data/hound_tamer.json`](sample_data/hound_tamer.json)
- Issue Discussion: https://claude.ai/share/ae2f55df-236b-4c8e-b67f-043492a62176
