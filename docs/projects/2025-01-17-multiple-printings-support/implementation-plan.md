# Implementation Plan for Multiple Printings Support

This document provides a detailed implementation plan for adding multiple card printings and unique search modes to Scryfall OS.

## Project Timeline: 6 Weeks

### Week 1-2: Database Schema and Migration
### Week 3-4: API Enhancement and Core Logic  
### Week 5-6: Performance Optimization and Deployment

---

## Phase 1: Database Schema (Week 1-2)

### Week 1: Schema Design and Creation

#### Day 1-2: Schema Implementation
- [ ] Create new migration file: `2025-01-20-01-multiple-printings-schema.sql`
- [ ] Implement `magic.oracle_cards` table with constraints
- [ ] Implement `magic.card_printings` table with foreign keys
- [ ] Add comprehensive indexes for performance
- [ ] Create backwards compatibility view `magic.cards`

#### Day 3-4: Data Import Tools
- [ ] Create bulk data import script for `default_cards`
- [ ] Implement Oracle ID deduplication logic
- [ ] Add data validation and integrity checks  
- [ ] Create incremental update mechanism

#### Day 5: Testing and Validation
- [ ] Test schema creation and constraints
- [ ] Validate backwards compatibility view
- [ ] Import subset of data for testing
- [ ] Performance baseline testing

### Week 2: Full Data Migration

#### Day 1-2: Complete Data Import
- [ ] Download latest `default_cards` bulk data (~514MB)
- [ ] Full import to new schema (~800k printings)
- [ ] Data integrity validation and cleanup
- [ ] Index creation and optimization

#### Day 3-4: Migration Testing  
- [ ] Test existing API endpoints with new schema
- [ ] Validate query performance meets targets
- [ ] Compare results with current implementation
- [ ] Fix any data quality issues

#### Day 5: Migration Validation
- [ ] Complete performance comparison
- [ ] Validate all existing functionality works
- [ ] Document migration process
- [ ] Prepare rollback procedures

**Deliverables**: 
- New database schema with full data
- Migration scripts and documentation
- Performance validation report
- Backwards compatibility confirmation

---

## Phase 2: API Enhancement (Week 3-4)

### Week 3: Core API Changes

#### Day 1-2: Parameter Parsing and Validation
- [ ] Add `unique` parameter to search endpoint
- [ ] Implement parameter validation (`cards`/`art`/`prints`)
- [ ] Add backwards compatibility (default to `cards`)
- [ ] Update error handling for invalid parameters

#### Day 3-4: Query Generation Logic
- [ ] Modify SQL generation for `unique=cards` mode
- [ ] Implement `unique=art` query logic  
- [ ] Implement `unique=prints` query logic
- [ ] Add proper ordering and DISTINCT ON clauses

#### Day 5: Response Format Enhancement
- [ ] Update response JSON structure
- [ ] Add printing-specific fields (set, artist, etc.)
- [ ] Include unique mode metadata in response
- [ ] Maintain backwards compatibility

### Week 4: Advanced Features

#### Day 1-2: Enhanced Search Syntax
- [ ] Add `set:` search parameter for specific sets
- [ ] Add printing-specific attributes (artist, frame, etc.)
- [ ] Update query parser to handle new syntax
- [ ] Test complex multi-attribute searches

#### Day 3-4: Pagination and Caching
- [ ] Implement unique-mode-aware caching
- [ ] Add pagination support for `unique=prints`
- [ ] Update cache keys to include unique mode
- [ ] Test cache hit rates and performance

#### Day 5: Testing and Validation
- [ ] Comprehensive API testing for all unique modes
- [ ] Validate response formats and data accuracy
- [ ] Performance testing for complex queries
- [ ] Integration testing with existing clients

**Deliverables**:
- Enhanced search API with unique modes
- Updated response format with printing data
- Comprehensive test coverage
- Performance validation

---

## Phase 3: Performance Optimization (Week 5-6)

### Week 5: Performance Tuning

#### Day 1-2: Query Optimization
- [ ] Analyze query execution plans for all modes
- [ ] Optimize indexes based on real usage patterns
- [ ] Tune database configuration for larger dataset
- [ ] Add query performance monitoring

#### Day 3-4: Application Performance
- [ ] Optimize response serialization for larger datasets
- [ ] Implement efficient result limiting and pagination
- [ ] Add application-level performance monitoring
- [ ] Memory usage optimization

#### Day 5: Load Testing
- [ ] Comprehensive load testing with realistic workloads
- [ ] Test concurrent user scenarios
- [ ] Validate performance targets (50ms query, 70ms response)
- [ ] Stress test with peak traffic simulation

### Week 6: Documentation and Deployment

#### Day 1-2: Documentation
- [ ] Update API documentation with unique parameter
- [ ] Create migration guide for API consumers
- [ ] Document new search syntax and examples
- [ ] Performance tuning guide for operations

#### Day 3-4: Deployment Preparation
- [ ] Production deployment strategy
- [ ] Feature flags for gradual rollout
- [ ] Monitoring and alerting setup
- [ ] Rollback procedures and testing

#### Day 5: Production Deployment
- [ ] Deploy to staging environment
- [ ] Final validation testing
- [ ] Production deployment with monitoring
- [ ] User communication and announcement

**Deliverables**:
- Performance-optimized implementation
- Complete documentation
- Production deployment
- Monitoring and alerting

---

## Technical Implementation Details

### Database Migration Script Structure

```sql
-- File: api/db/2025-01-20-01-multiple-printings-schema.sql

-- Create new tables
CREATE TABLE magic.oracle_cards (...);
CREATE TABLE magic.card_printings (...);

-- Create indexes
CREATE INDEX idx_oracle_cards_name_gin ...;
CREATE INDEX idx_card_printings_oracle_id ...;
-- ... additional indexes

-- Create backwards compatibility view
CREATE VIEW magic.cards AS 
SELECT ... FROM magic.oracle_cards oc
JOIN LATERAL (
    SELECT * FROM magic.card_printings cp 
    WHERE cp.oracle_id = oc.oracle_id
    ORDER BY cp.released_at DESC LIMIT 1
) cp ON true;
```

### API Code Changes

#### Parameter Handling
```python
# In api_resource.py
def search(self, *, q: str = None, unique: str = "cards", **kwargs):
    if unique not in ["cards", "art", "prints"]:
        raise falcon.HTTPBadRequest(
            title="Invalid Parameter",
            description=f"unique must be one of: cards, art, prints. Got: {unique}"
        )
    
    return self._search(query=q, unique_mode=unique, **kwargs)
```

#### Query Generation
```python
def _generate_search_query(self, parsed_query, unique_mode: str, limit: int):
    if unique_mode == "cards":
        return self._generate_cards_query(parsed_query, limit)
    elif unique_mode == "art":
        return self._generate_art_query(parsed_query, limit)
    elif unique_mode == "prints":
        return self._generate_prints_query(parsed_query, limit)
```

### Data Import Process

```python
# scripts/import_default_cards.py
async def import_default_cards():
    # Download latest default_cards bulk data
    bulk_data = await download_scryfall_bulk("default_cards")
    
    # Process in batches for memory efficiency
    async for batch in process_cards_batch(bulk_data, batch_size=1000):
        oracle_cards, printings = separate_oracle_and_printings(batch)
        
        # Upsert oracle cards (deduplicated by oracle_id)
        await upsert_oracle_cards(oracle_cards)
        
        # Insert printings (unique by set + collector number)
        await insert_card_printings(printings)
```

## Risk Management

### Technical Risks

**Database Performance Degradation**
- *Probability*: Medium
- *Impact*: High  
- *Mitigation*: Extensive performance testing, rollback plan
- *Timeline Impact*: Could delay deployment by 1 week

**Data Quality Issues**
- *Probability*: Medium
- *Impact*: Medium
- *Mitigation*: Comprehensive validation, staged rollout
- *Timeline Impact*: Additional testing time needed

**API Compatibility Issues**
- *Probability*: Low
- *Impact*: High
- *Mitigation*: Backwards compatibility testing, feature flags
- *Timeline Impact*: Could require API redesign

### Mitigation Strategies

1. **Phased Rollout**: Deploy with feature flags, enable gradually
2. **Performance Monitoring**: Real-time monitoring with automated rollback
3. **Extensive Testing**: Unit, integration, and load testing at each phase
4. **Documentation**: Clear migration guides for API consumers

## Success Criteria

### Technical Metrics
- [ ] All 800k+ card printings successfully imported
- [ ] 95th percentile query time < 50ms for all unique modes
- [ ] 95th percentile HTTP response time < 70ms
- [ ] No degradation in existing functionality

### Functional Requirements  
- [ ] `unique=cards` mode works identically to current API
- [ ] `unique=art` mode returns one result per unique artwork
- [ ] `unique=prints` mode returns all matching printings
- [ ] Enhanced search syntax works for sets and printing attributes

### Operational Requirements
- [ ] Zero-downtime deployment achieved
- [ ] Rollback procedures tested and documented
- [ ] Monitoring and alerting in place
- [ ] User documentation complete and published

## Post-Launch Activities

### Week 7: Monitoring and Optimization
- [ ] Monitor performance metrics and user feedback
- [ ] Optimize slow queries based on real usage
- [ ] Address any data quality issues
- [ ] Fine-tune cache sizes and TTLs

### Week 8: User Onboarding
- [ ] User communication about new features
- [ ] API consumer migration assistance
- [ ] Gather feedback and feature requests
- [ ] Plan future enhancements

---

**Project Success**: This implementation will provide comprehensive access to all Magic: The Gathering card printings while maintaining excellent performance and backwards compatibility.