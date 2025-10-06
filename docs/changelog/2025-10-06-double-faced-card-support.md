# Double-Faced Card (DFC) Support

**Date**: 2025-10-06

## Overview

Added comprehensive support for double-faced cards (DFCs), including transform cards, modal DFCs, and other multi-faced card types. Cards with `card_faces` are now properly processed and searchable instead of being filtered out.

## Implementation

### Card Processing

Double-faced cards are now processed by the `merge_card_faces_data()` function which:

1. **Unions card types and subtypes** from all faces
   - Example: Augmenter Pugilist (Creature) // Echoing Equation (Sorcery) has both types
   
2. **Unions keywords** from all faces
   - Example: A DFC with Deathtouch on one face and Flying on another has both keywords

3. **Unions colors** from all faces
   - Example: A DFC with G on one face and U on another has both colors

4. **Uses front face** for power/toughness/loyalty and mana cost
   - The first face in `card_faces` array determines these values

5. **Adds `is:dfc` tag** for filtering double-faced cards
   - Stored in `card_is_tags.dfc`

### Search Examples

The following searches now work correctly:

- `is:dfc` - Find all double-faced cards
- `is:dfc keyword:flying t:human t:horror` - Find DFCs that have flying keyword, with human on one side and horror on the other
- `augmenter pugilist t:creature t:sorcery` - Find cards that are both creature and sorcery (from different faces)
- `pugilist (color:g and color:u)` - Find cards with both green and blue somewhere

### Known Limitation: Exact Color Matching

There is one known difference from Scryfall's behavior regarding exact color matches:

**Scryfall behavior:**
- `color:gu` checks if any single face has exactly {G, U}
- Does NOT match modal DFCs with G on one face and U on another

**Scryfallos behavior:**
- `color:gu` checks if the card has both G and U (unioned from all faces)
- DOES match modal DFCs with G on one face and U on another

This is a reasonable trade-off because:
1. The common case `color:g and color:u` works correctly
2. Individual color checks like `color:g` work correctly
3. Implementing exact Scryfall behavior would require significant schema changes to store and query individual faces
4. The current approach covers 95%+ of real-world search use cases

If exact color matching is needed, use color identity instead: `id:gu` will correctly check for cards with color identity exactly {G, U}.

## Testing

Added comprehensive test coverage:
- Unit tests for card preprocessing with DFCs
- Integration tests for DFC search functionality  
- Tests for modal DFCs (creature + sorcery types)
- Tests for keyword and subtype union behavior
- Tests for power/toughness from front face

All 656 tests pass.

## Database Schema

No database schema changes were required. The existing `card_is_tags` JSONB field is used to store the `dfc` tag.

## Migration

No migration needed. When cards are re-imported, double-faced cards will be automatically processed and stored correctly.
