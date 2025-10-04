# Regex-Based Search Support

**Date**: 2025-10-04

## Overview

Implemented regex-based search functionality allowing users to search for Magic cards using regular expression patterns in Oracle text. This feature follows Scryfall's regex search syntax.

## Features

### Syntax

Regular expression searches use the `/pattern/` syntax with either the `regex:` or `re:` keyword:

```
regex:/pattern/
re:/pattern/
```

### Examples

Search for cards with "destroy" at the beginning of their oracle text:
```
regex:/^destroy/
```

Search for cards mentioning specific numbers using digit patterns:
```
re:/\d+/
```

Search for cards with either "flying" or "vigilance":
```
regex:/flying|vigilance/
```

Search for cards with "tap" followed by "creature":
```
regex:/tap.*creature/
```

### Combining with Other Searches

Regex searches can be combined with other search criteria:

```
regex:/flying/ cmc=3
regex:/destroy/ color:black
-regex:/hexproof/ power>5
```

## Implementation Details

- **Parser**: Added `RegexValueNode` to represent regex patterns in the AST
- **SQL Generation**: Uses PostgreSQL's `~*` operator for case-insensitive regex matching
- **Preprocessing**: Special handling in the query preprocessor to treat `/pattern/` as a single token
- **Escaped Characters**: Supports escaped forward slashes in patterns: `/test\/pattern/`

## Technical Notes

### Database Column

Regex searches apply to the `oracle_text` column, same as regular oracle text searches but using pattern matching instead of word-based ILIKE searches.

### PostgreSQL Operator

The implementation uses PostgreSQL's `~*` operator which provides case-insensitive POSIX regex matching. For example:

```sql
card.oracle_text ~* 'flying|vigilance'
```

### Differences from Text Search

Regular text searches using `oracle:flying` use SQL `ILIKE` with wildcards (`%flying%`), while regex searches using `regex:/flying/` use the exact pattern provided with PostgreSQL regex operators.

## Limitations

- Currently limited to Scryfall extensions are not implemented (per design decision)
- Only applies to oracle text field via `regex:` or `re:` keywords
- Pattern syntax follows PostgreSQL POSIX regex standard

## Testing

- 14 new tests for regex pattern parsing
- 12 new tests for SQL generation
- All 471 existing tests continue to pass
- Comprehensive test coverage for:
  - Basic pattern parsing
  - Complex patterns (anchors, character classes, alternation)
  - Escaped slashes
  - Combination with other search operators
  - Negation
  - SQL generation correctness

## Future Enhancements

Potential future enhancements (not in current scope):
- Support for Scryfall extensions (if needed)
- Additional text fields (name, type, flavor text)
- Case-sensitive regex option using `~` operator
