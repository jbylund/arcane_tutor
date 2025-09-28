# All Printings Ingestion and Unique/Prefer Support Project

**Project Date:** 2025-09-28  
**Status:** Planning Phase  
**Target Completion:** Q1 2026

## Table of Contents

1. [Overview](#overview)
2. [Current State Analysis](#current-state-analysis)
3. [Technical Design](#technical-design)
4. [Data Size Estimation](#data-size-estimation)
5. [Performance Requirements](#performance-requirements)
6. [Implementation Plan](#implementation-plan)
7. [Risk Assessment](#risk-assessment)
8. [References](#references)

## Overview

This project aims to enhance Scryfall OS to support ingesting all printings of Magic: The Gathering cards and implement the `unique` and `prefer` search parameters that match Scryfall's functionality. Currently, the system stores only one version of each card (typically the Oracle version), but this enhancement will allow users to:

- View all printings of a card across different sets
- Control display preferences via `unique:cards`, `unique:art`, and `unique:prints` parameters  
- Use `prefer:newest` or `prefer:oldest` to control which printing is shown for unique results
- Support set-specific searches and collector number queries

## Current State Analysis

### Current Database Schema
- Single card per `card_name` with `UNIQUE INDEX idx_cards_name`
- Set information in `card_set_code` column (single set per card)
- Collector number in `collector_number` and `collector_number_int` columns
- Card data stored in `raw_card_blob` jsonb field

### Current API Behavior
- Uses `unique:prints` in Scryfall API queries during ingestion
- Deduplicates by card name in `_get_cards_to_insert()` method
- Returns single card per name in search results

### Current Data Size
Based on existing implementation, the system currently handles the Oracle card dataset equivalent.

## Technical Design

See detailed technical specifications in:
- [Database Schema Design](./database-schema.md)
- [API Changes Design](./api-changes.md)  
- [Ingestion Strategy](./ingestion-strategy.md)
- [Data Size Analysis](./data-analysis.md)
- [Implementation Timeline](./implementation-timeline.md)

## Data Size Estimation

**Actual Scryfall Bulk Data Analysis (2025):**
- Oracle Cards: 156.7 MB (~31,000 unique card names)
- Default Cards: 490.2 MB (~95,000 standard printings, **3.13x ratio**)
- All Cards: 2,281.5 MB (~150,000+ total objects, **14.6x ratio**)

**Projected Impact (Phase 1 - Default Cards):**
- Database size increase: **3.13x current size**
- Storage: From ~2GB to **~7.8GB** (includes indexes)
- Memory usage: Cache requirements increase to ~1.6GB
- Query performance: 15-25% degradation for complex searches

See detailed analysis in [Data Size Analysis](./data-analysis.md)

## Performance Requirements

- **Search Latency Target:** < 50ms for search query execution
- **HTTP Request Target:** < 70ms for complete request cycle
- **Ingestion Performance:** Maintain reasonable bulk import speeds
- **Storage Efficiency:** Minimize duplication while supporting all printings

## Implementation Plan

### Phase 1: Database Schema Migration (4-6 weeks)
- [ ] Design new schema supporting multiple printings
- [ ] Create migration scripts with rollback capability
- [ ] Test migration with subset of data
- [ ] Performance testing and optimization

### Phase 2: Ingestion Pipeline Updates (3-4 weeks)  
- [ ] Modify ingestion to store all printings
- [ ] Update deduplication logic
- [ ] Add set-specific import capabilities
- [ ] Bulk import testing and validation

### Phase 3: Search API Enhancement (4-5 weeks)
- [ ] Implement unique parameter support
- [ ] Add prefer parameter functionality  
- [ ] Update query parsing and SQL generation
- [ ] Performance optimization and indexing

### Phase 4: Testing and Validation (2-3 weeks)
- [ ] Comprehensive test suite updates
- [ ] Performance benchmarking
- [ ] Data integrity validation
- [ ] User acceptance testing

**Total Estimated Timeline:** 18 weeks (4.5 months)

See detailed implementation plan in [Implementation Timeline](./implementation-timeline.md)

## Risk Assessment

### High Risk
- **Database Migration Complexity:** Schema changes on production data
- **Performance Degradation:** 3x data increase may impact query speed
- **Storage Costs:** Significant increase in database storage requirements

### Medium Risk  
- **API Compatibility:** Ensuring backward compatibility during transition
- **Ingestion Reliability:** Handling larger data volumes and edge cases
- **Cache Invalidation:** Managing increased cache complexity

### Mitigation Strategies
- Staged rollout with feature flags
- Comprehensive performance testing
- Database partitioning strategies
- Enhanced monitoring and alerting

## References

- [Scryfall Display Syntax](https://scryfall.com/docs/syntax#display)
- [Scryfall Bulk Data API](https://scryfall.com/docs/api/bulk-data)
- [Current Schema Documentation](../../db/2025-08-08-schema.sql)
- [Existing API Implementation](../../../api/api_resource.py)