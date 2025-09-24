# Improve Text Field Operator Handling Architecture

## Problem

Currently, the system uses a hard-coded approach to determine whether text fields should use exact matching (`=`) or pattern matching (`ILIKE`) for the colon operator (`:`). This is implemented in `_handle_colon_operator()` in `scryfall_nodes.py`:

```python
# Handle fields that need exact matching instead of pattern matching
if attr in ("card_set_code", "card_layout", "card_border"):
    if self.operator == ":":
        self.operator = "="
    return super().to_sql(context)

# Regular text field handling with pattern matching
return self._handle_text_field_pattern_matching(context, lhs_sql)
```

This approach has several issues:

1. **Not Scalable**: Adding new exact-match text fields requires code changes in multiple places
2. **Hard to Maintain**: Logic is scattered and field behavior isn't declared alongside field definitions
3. **Error Prone**: Easy to forget to add new fields to the hard-coded list
4. **Not Self-Documenting**: Field behavior isn't clear from the field definition

## Current Field Usage Patterns

**Exact Match Fields** (should use `=`):
- `card_set_code` - Set codes are exact identifiers (e.g., "ktk", "bfz")
- `card_layout` - Layout types are exact values (e.g., "normal", "split", "transform") 
- `card_border` - Border colors are exact values (e.g., "black", "white", "borderless")
- `collector_number` - When using `:` operator (exact text match)

**Pattern Match Fields** (should use `ILIKE`):
- `card_name` - Users want partial matching (e.g., "lightning" matches "Lightning Bolt")
- `card_artist` - Users want partial matching for artist names
- `oracle_text` - Users want to search for text fragments
- `flavor_text` - Users want to search for text fragments

## Proposed Solutions

### Option 1: New FieldType.TEXT_EXACT

Add a new field type to distinguish exact vs pattern matching text fields:

```python
class FieldType(StrEnum):
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"           # Pattern matching with ILIKE
    TEXT_EXACT = "text_exact"  # Exact matching with =
```

**Pros:**
- Clear semantic distinction in field type
- Simple to implement and understand
- Field behavior is explicit in field definition

**Cons:**
- Adds complexity to the type system
- May confuse developers (both are still text columns in DB)

### Option 2: Add Operator Strategy to FieldInfo

Extend the `FieldInfo` class to specify operator handling:

```python
class OperatorStrategy(StrEnum):
    EXACT = "exact"      # : becomes =
    PATTERN = "pattern"  # : becomes ILIKE with wildcards

class FieldInfo:
    def __init__(self, db_column_name: str, field_type: FieldType, search_aliases: list[str], 
                 parser_class: ParserClass | None = None, operator_strategy: OperatorStrategy = OperatorStrategy.PATTERN):
        # ...
        self.operator_strategy = operator_strategy
```

**Usage:**
```python
FieldInfo("card_set_code", FieldType.TEXT, ["set", "s"], ParserClass.TEXT, OperatorStrategy.EXACT),
FieldInfo("card_layout", FieldType.TEXT, ["layout"], ParserClass.TEXT, OperatorStrategy.EXACT),
FieldInfo("card_name", FieldType.TEXT, ["name"], ParserClass.TEXT, OperatorStrategy.PATTERN),
```

**Pros:**
- Behavior is declared alongside field definition
- Easy to extend with new operator strategies
- Backward compatible (defaults to pattern matching)
- More flexible than field types

**Cons:**
- Adds parameter complexity to FieldInfo
- Need to update all field definitions

### Option 3: Hybrid Approach

Use FieldType for the primary distinction but allow overrides:

```python
class FieldType(StrEnum):
    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object" 
    NUMERIC = "numeric"
    TEXT = "text"         # Default: pattern matching
    TEXT_EXACT = "text_exact"  # Default: exact matching

# Allow override with operator_strategy parameter when needed
```

## Recommendation

**Option 2** (OperatorStrategy) seems most flexible and maintainable:

1. **Self-Documenting**: Field behavior is clear from definition
2. **Extensible**: Easy to add new operator strategies (e.g., case-sensitive, regex)
3. **Maintainable**: No scattered hard-coded lists
4. **Flexible**: Can override default behavior per field

## Implementation Plan

1. Add `OperatorStrategy` enum to `db_info.py`
2. Extend `FieldInfo` class with `operator_strategy` parameter
3. Update all field definitions to specify strategy
4. Update `_handle_colon_operator()` to use field strategy instead of hard-coded list
5. Add tests to verify behavior
6. Update documentation

## Breaking Changes

This would be a breaking change requiring updates to all `FieldInfo` definitions, but could be made backward-compatible with sensible defaults.

## Related

- Issue #203 (Layout and Border Search Support) - where this issue was discovered
- The current implementation works but isn't architecturally sound for future maintenance