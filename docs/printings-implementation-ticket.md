# Implement All Card Printings Support + Unique Search Modes

## Summary

Upgrade Scryfall OS to support all printings of Magic cards (not just one per card name) and implement Scryfall's unique search modes: `unique:cards`, `unique:art`, `unique:prints`, and no unique parameter.

## Current Limitation

- Database stores only 1 printing per card name due to `UNIQUE INDEX idx_cards_name`
- API hardcodes `"unique:prints"` when fetching from Scryfall
- Lightning Bolt example: 1 stored vs 55+ available printings
- Missing price comparison across different printings
- No support for set-specific or artwork-specific searches

## Proposed Solution

**See detailed design document**: `/docs/changelog/2025-01-27-card-printings-support.md`

### High Level Changes

1. **Database Schema**: Replace single `magic.cards` table with `magic.card_printings` that stores all printings individually, keyed by Scryfall ID with Oracle ID grouping

2. **API Enhancement**: Add `unique` parameter support to search endpoint:
   - `unique=cards` (default): One result per Oracle ID (functional reprint)  
   - `unique=art`: One result per artwork (illustration_id)
   - `unique=prints`: All printings individually
   - No unique param: All printings

3. **Data Migration**: Bulk import all printings from Scryfall (~500k+ cards instead of ~25k unique names)

4. **UI Updates**: Enhanced result display showing printing-specific info (set, collector number, pricing per printing)

### Implementation Phases

1. **Schema Design & Migration** (2 weeks)
2. **Data Import & Validation** (1 week) 
3. **API & Query Updates** (1-2 weeks)
4. **UI & Testing** (1-2 weeks)

### Benefits

- Full Scryfall API compatibility for card searching
- Price comparison across printings
- Set-specific and artwork-specific queries  
- Collection management with specific printings
- Enhanced search experience matching official Scryfall

### Technical Requirements

- PostgreSQL 12+ for JSONB performance
- ~55x storage increase (well-indexed for performance)  
- Scryfall bulk data API integration
- Comprehensive query optimization

### Success Criteria

- [x] Support all 4 unique modes
- [x] >95% printing coverage vs Scryfall
- [x] <2s query response times
- [x] 100% API syntax compatibility  
- [x] Backward compatibility during migration

**Priority**: High - Core feature gap limiting user functionality
**Effort**: Large (4-6 weeks) - Database redesign with data migration  
**Dependencies**: Storage scaling, performance optimization

---

**Action Item**: Review detailed design document and approve for implementation