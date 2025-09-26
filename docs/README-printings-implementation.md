# Card Printings Implementation - Document Index

This directory contains comprehensive documentation for implementing support for all Magic card printings and unique search modes in Scryfall OS.

## Core Documents

### 1. [Implementation Ticket](printings-implementation-ticket.md)
**Purpose**: Executive summary and issue description  
**Audience**: Project managers, stakeholders  
**Key Content**:
- Problem statement and current limitations
- High-level solution overview  
- Implementation phases and timeline
- Success criteria and risk assessment

### 2. [Detailed Design Document](changelog/2025-01-27-card-printings-support.md)
**Purpose**: Comprehensive technical specification  
**Audience**: Engineers, architects, reviewers  
**Key Content**:
- Complete database schema design
- API endpoint specifications
- Data migration strategy
- UI/UX enhancements
- Performance considerations
- Risk mitigation plans

### 3. [Migration Plan](printings-migration-plan.md)
**Purpose**: Step-by-step technical implementation guide  
**Audience**: Implementation team, DevOps  
**Key Content**:
- Detailed migration scripts and SQL
- Phase-by-phase implementation steps
- Testing and validation procedures
- Deployment and rollback strategies
- Performance benchmarking

## Implementation Overview

### Current State
- **Database**: Single card per name (`UNIQUE INDEX idx_cards_name`)
- **Storage**: ~25k unique card names  
- **API**: Hardcoded `unique:prints` when fetching from Scryfall
- **Limitation**: No support for multiple printings or unique modes

### Target State  
- **Database**: All printings stored individually (~500k+ records)
- **Schema**: `magic.card_printings` table with Scryfall ID primary key
- **API**: Full support for `unique=cards|art|prints|none` parameter
- **Features**: Price comparison, set-specific searches, artwork variants

### Key Benefits
1. **Full Scryfall API compatibility** for search functionality
2. **Enhanced user experience** with printing-specific data
3. **Collection management** capabilities with specific printings
4. **Price comparison** across different printings of the same card
5. **Advanced filtering** by set, artist, frame style, etc.

## Technical Architecture

### Database Changes
```sql
-- New primary table
magic.card_printings (
    scryfall_id uuid PRIMARY KEY,        -- Unique per printing
    oracle_id uuid,                      -- Groups functional reprints  
    illustration_id uuid,                -- Groups same artwork
    -- ... printing-specific columns
)

-- Backward compatibility
CREATE VIEW magic.cards AS SELECT DISTINCT ON (oracle_id) ...
```

### API Enhancements
```http
GET /search?q=lightning%20bolt&unique=cards    # One per Oracle ID
GET /search?q=lightning%20bolt&unique=art      # One per artwork
GET /search?q=lightning%20bolt&unique=prints   # All printings
GET /search?q=lightning%20bolt                 # Default: cards mode
```

## Implementation Timeline

| Phase | Duration | Key Deliverables |
|-------|----------|------------------|
| **Schema Design** | 1-2 weeks | Database migrations, indexes, views |
| **Data Migration** | 1 week | Bulk import, validation, integrity checks |  
| **API Updates** | 1-2 weeks | Endpoint changes, query parser updates |
| **UI & Testing** | 1-2 weeks | Interface updates, comprehensive testing |
| **Total** | **4-6 weeks** | Full printings support deployed |

## Quality Assurance

### Success Metrics
- ✅ Support all 4 unique modes (`cards`, `art`, `prints`, `none`)
- ✅ >95% printing coverage compared to official Scryfall
- ✅ <2s query response times for typical searches  
- ✅ 100% backward compatibility during migration
- ✅ Zero data loss during schema transition

### Testing Strategy
- **Unit Tests**: Parser, SQL generation, API endpoints
- **Integration Tests**: End-to-end search functionality
- **Performance Tests**: Query response times, concurrent users
- **Data Validation**: Integrity checks, Scryfall comparison
- **Load Testing**: Large dataset performance under load

## Next Steps

1. **Review Documents**: Stakeholder review of all three documents
2. **Approve Implementation**: Go/no-go decision on the feature  
3. **Resource Allocation**: Assign engineering team and timeline
4. **Begin Phase 1**: Start with schema design and migration scripts

## Questions and Feedback

For questions about this implementation plan, please review:
- Technical details → [Design Document](changelog/2025-01-27-card-printings-support.md)  
- Implementation steps → [Migration Plan](printings-migration-plan.md)
- Project scope → [Implementation Ticket](printings-implementation-ticket.md)

---
**Document Version**: 1.0  
**Last Updated**: 2025-01-27  
**Status**: Draft - Awaiting Review