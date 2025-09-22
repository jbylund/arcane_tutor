# Scryfall OS

An open source implementation of scryfall.
This project contains a few pieces:

1. The parsing library for the scryfall search DSL
1. Tooling for turning the parsed scryfall DSL into a database query
1. Tooling for loading a scryfall bulk data export into a postgres database (and hopefully for incrementally pulling in new cards, though the scale is probably sufficiently small that it doesn't matter)
1. a simple html + vanilla javascript web app which allows users to search for cards and have them displayed in an interface similar scryfall
1. **Card tagging system** - Import and manage Scryfall's card tags with hierarchy support

## Card Tagging Features

The API now supports importing card tags from Scryfall's tagger system:

### Available Endpoints

- `update_tagged_cards` - Import cards for a specific tag
- `discover_and_import_all_tags` - Bulk import all available tags and their card associations

### Usage Examples

```bash
# Import cards for a specific tag
curl "http://localhost:8080/update_tagged_cards?tag=flying"

# Discover and import all tags (cards only, no hierarchy)
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=true&import_hierarchy=false"

# Import tag hierarchy only (no card associations)
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=false&import_hierarchy=true"

# Full import: all tags, cards, and hierarchy relationships
curl "http://localhost:8080/discover_and_import_all_tags?import_cards=true&import_hierarchy=true"
```

### Database Schema

The tagging system uses two main database components:

1. **magic.cards.card_tags** (jsonb) - Stores tag associations for each card
2. **magic.card_tags** table - Stores tag hierarchy with parent-child relationships

### Rate Limiting

The bulk import includes built-in rate limiting:
- 200ms delay between individual tag imports
- 500ms delay between hierarchy relationship requests
- Progress logging every 50 tags processed

Need to consolidate:
1. database column names
1. interface column names
