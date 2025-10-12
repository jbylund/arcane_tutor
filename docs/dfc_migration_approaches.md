# DFC Schema Migration Approaches

This document outlines different approaches for migrating queries from `magic.cards` to the DFC-aware schema.

## Background

The `s_dfc` schema was created to normalize DFC (Double-Faced Card) data, storing face-specific attributes separately from card-level and print-level attributes. The main table is `s_dfc.cards_with_prints` which contains two composite-type columns:
- `card_info`: Card-level data (name, color identity, keywords, edhrec_rank) plus `front_face` and `back_face` composites
- `print_info`: Print-level data (set, prices, legalities) plus `front_face` and `back_face` composites for print-specific attributes

## Approaches

### Approach 1: Use s_dfc.cards_flat View (RECOMMENDED)

**Description**: Query the flattened view `s_dfc.cards_flat` which extracts commonly-used fields.

**Pros**:
- ✅ Queries remain readable and similar to current structure
- ✅ Easier migration with minimal code changes
- ✅ PostgreSQL can optimize view queries efficiently
- ✅ Still have access to composites when needed for back face

**Cons**:
- ⚠️ Slightly less control over query execution
- ⚠️ View might add minimal overhead (usually negligible)

**Example**:
```sql
-- Simple and readable
SELECT card_name, creature_power, mana_cost_text
FROM s_dfc.cards_flat
WHERE creature_power > 3;

-- Access back face when needed
SELECT 
    card_name,
    ((card_info).front_face).face_creature_power AS front_power,
    ((card_info).back_face).face_creature_power AS back_power
FROM s_dfc.cards_flat
WHERE card_name LIKE '%Hound%';
```

**Migration effort**: LOW - Most queries need minimal changes

### Approach 2: Use s_dfc.cards_with_prints Directly

**Description**: Query the base table using composite type syntax throughout.

**Pros**:
- ✅ Most direct and explicit
- ✅ Maximum control over query execution
- ✅ No intermediate view layer

**Cons**:
- ❌ Very verbose - every field access requires nested syntax
- ❌ Harder to read and maintain
- ❌ Significant code changes required

**Example**:
```sql
-- Verbose and complex
SELECT 
    ((card_info).card_name) AS card_name,
    ((card_info).front_face).face_creature_power AS creature_power,
    ((card_info).front_face).face_mana_cost_text AS mana_cost_text
FROM s_dfc.cards_with_prints AS card
WHERE ((card_info).front_face).face_creature_power > 3
   OR ((card_info).back_face).face_creature_power > 3;
```

**Migration effort**: HIGH - Every query needs extensive rewriting

### Approach 3: Hybrid (Keep magic.cards for some queries)

**Description**: Use s_dfc for searches, keep magic.cards for admin/stats queries.

**Pros**:
- ✅ Minimal disruption to non-search code
- ✅ Faster migration of critical path

**Cons**:
- ❌ Two schemas to maintain
- ❌ Confusion about which to use when
- ❌ magic.cards still has duplicated face data

**Migration effort**: MEDIUM - Selective migration

## Recommendation

**Use Approach 1 (s_dfc.cards_flat view)** for the following reasons:

1. **Balance**: Gets benefits of normalized schema without excessive complexity
2. **Performance**: PostgreSQL optimizes views well, minimal overhead
3. **Maintainability**: Queries remain readable and similar to current code
4. **Flexibility**: Can still access composite data when needed
5. **Migration**: Easier to implement and test incrementally

## Implementation Plan (Approach 1)

### Phase 1: Infrastructure ✅ COMPLETE
- [x] Create s_dfc.cards_flat view
- [x] Add indexes on common query columns
- [x] Document the view structure

### Phase 2: Update Query Layer
1. Update WHERE clause generation to handle face-level attributes with OR
   - Already done in scryfall_nodes.py
2. Update SELECT clauses in api_resource.py
   - Replace `magic.cards` → `s_dfc.cards_flat`
   - Field names mostly stay the same (except face-specific ones)
3. Update simple queries (COUNT, admin functions)
   - Direct replacement with view

### Phase 3: Testing
1. Update integration tests to use new schema
2. Verify DFC queries work correctly
3. Test single-faced cards still work (back_face is NULL)
4. Performance benchmarking

### Phase 4: Cleanup
1. Update documentation
2. Consider deprecating direct magic.cards access
3. Add migration notes

## SQL Examples Comparison

### Current (magic.cards):
```sql
SELECT card_name, creature_power, type_line
FROM magic.cards
WHERE creature_power > 3
  AND card_name LIKE '%Werewolf%'
ORDER BY creature_power DESC;
```

### Approach 1 (s_dfc.cards_flat):
```sql
SELECT card_name, creature_power, type_line
FROM s_dfc.cards_flat
WHERE (creature_power > 3 
    OR ((card_info).back_face).face_creature_power > 3)
  AND card_name LIKE '%Werewolf%'
ORDER BY creature_power DESC;
```

### Approach 2 (s_dfc.cards_with_prints):
```sql
SELECT 
    ((card_info).card_name) AS card_name,
    ((card_info).front_face).face_creature_power AS creature_power,
    ((card_info).front_face).face_type_line AS type_line
FROM s_dfc.cards_with_prints
WHERE (((card_info).front_face).face_creature_power > 3
    OR ((card_info).back_face).face_creature_power > 3)
  AND ((card_info).card_name) LIKE '%Werewolf%'
ORDER BY ((card_info).front_face).face_creature_power DESC;
```

## Decision - FINAL ARCHITECTURE

**Decision Made**: Use Approach 2 (s_dfc.cards_with_prints with composite types) for search queries, keep magic.cards for data storage/admin.

### Final Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Data Flow                             │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Scryfall API → card_processing.py → magic.cards        │
│                    (Data Ingestion)    (Raw Storage)     │
│                                                          │
│  magic.cards → DFC Migration → s_dfc.*                  │
│  (Raw Storage)  (2025-10-12)   (Normalized Views)       │
│                                                          │
│  s_dfc.cards_with_prints → Search Queries → Results     │
│  (Query Layer with composites)                           │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### Responsibilities

**magic.cards** (Raw Data Layer):
- ✅ Card ingestion from Scryfall API
- ✅ Data export/import operations
- ✅ Administrative operations (DELETE, COUNT for stats)
- ✅ Raw data storage with denormalized face rows
- ✅ Continues to store one row per face per printing

**s_dfc.cards_with_prints** (Query Layer):
- ✅ Search queries (WHERE clauses with face-level predicates)
- ✅ Display results (SELECT with front face as default)
- ✅ Composite type access for both faces
- ✅ Normalized view of card/print/face data

### Implementation Status

✅ **COMPLETE** - Main search query migrated to use s_dfc.cards_with_prints
✅ **COMPLETE** - SQL generation handles composite types and face-level OR expansion
✅ **CORRECT** - Admin queries continue using magic.cards as intended

This architecture provides:
- Clean separation between storage and querying
- Programmatic query generation handles composite type verbosity
- Raw data always available in magic.cards for troubleshooting
- Normalized query layer for DFC-aware searches
