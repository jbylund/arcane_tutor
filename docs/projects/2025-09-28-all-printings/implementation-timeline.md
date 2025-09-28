# Implementation Timeline and Milestones

**Document:** Implementation Timeline  
**Project:** 2025-09-28 All Printings  
**Last Updated:** 2025-09-28

## Table of Contents

1. [Project Phases Overview](#project-phases-overview)
2. [Detailed Phase Breakdown](#detailed-phase-breakdown)
3. [Critical Dependencies](#critical-dependencies)
4. [Risk Mitigation Schedule](#risk-mitigation-schedule)
5. [Success Metrics](#success-metrics)

## Project Phases Overview

### Phase 1: Database Schema Migration (Weeks 1-6)
**Goal:** Prepare database to support multiple printings per card
- Database schema design and migration scripts
- Data model updates for printing_id, oracle_id, release_date, artwork_id
- Performance testing with existing data
- Rollback procedures

### Phase 2: Ingestion Pipeline Updates (Weeks 7-10)
**Goal:** Modify data ingestion to store all printings
- Remove card name deduplication logic
- Implement printing_id based storage
- Bulk import optimization for larger datasets
- Data validation and quality checks

### Phase 3: Search API Enhancement (Weeks 11-15)
**Goal:** Implement unique and prefer parameter support
- Query parser updates for unique:cards/art/prints
- SQL generation changes for prefer:newest/oldest
- API endpoint modifications
- Caching strategy updates

### Phase 4: Testing and Validation (Weeks 16-18)
**Goal:** Comprehensive testing and performance validation
- Unit and integration test updates
- Performance benchmarking
- Data integrity validation
- User acceptance testing

**Total Timeline:** 18 weeks (~4.5 months)

## Detailed Phase Breakdown

### Phase 1: Database Schema Migration (6 weeks)

#### Week 1-2: Schema Design and Planning
- [ ] **Week 1:** Database design finalization
  - Review [database-schema.md](./database-schema.md) design
  - Create migration SQL scripts with proper rollback
  - Design new indexes for optimal query performance
  - Plan storage and backup requirements

- [ ] **Week 2:** Migration script development
  - Write schema migration with proper constraints
  - Implement data population from raw_card_blob
  - Create rollback scripts and procedures
  - Test migration scripts on development data

#### Week 3-4: Migration Testing
- [ ] **Week 3:** Development environment testing
  - Execute migration on test dataset
  - Validate data integrity and constraints  
  - Performance test queries with new schema
  - Identify and fix migration issues

- [ ] **Week 4:** Staging environment validation
  - Deploy migration to staging environment
  - Load representative dataset for testing
  - Performance benchmarking of new vs old schema
  - Validate rollback procedures work correctly

#### Week 5-6: Production Migration Preparation
- [ ] **Week 5:** Production migration planning
  - Schedule production maintenance window
  - Prepare monitoring and alerting for migration
  - Create detailed migration runbook
  - Final staging environment validation

- [ ] **Week 6:** Production migration execution
  - Execute production database migration
  - Validate migration success and data integrity
  - Monitor performance post-migration
  - Update monitoring dashboards for new schema

### Phase 2: Ingestion Pipeline Updates (4 weeks)

#### Week 7-8: Core Ingestion Changes
- [ ] **Week 7:** Remove deduplication logic
  - Modify `_get_cards_to_insert()` to keep all printings
  - Update `_load_cards_with_staging()` for printing_id based upserts
  - Implement enhanced card preprocessing for new fields
  - Unit tests for ingestion changes

- [ ] **Week 8:** Bulk import optimization
  - Implement batch processing for large datasets
  - Add progress reporting and resume capability
  - Memory optimization for handling 95k+ cards
  - Error handling and retry logic improvements

#### Week 9-10: Data Validation and Testing  
- [ ] **Week 9:** Ingestion testing and validation
  - Test full re-ingestion with Default Cards dataset
  - Validate data quality and completeness
  - Performance testing of bulk import process
  - Create incremental update functionality

- [ ] **Week 10:** Integration and monitoring
  - Integrate with existing import workflows
  - Add monitoring for ingestion health metrics
  - Create alerts for failed imports or data quality issues
  - Documentation updates for new ingestion process

### Phase 3: Search API Enhancement (5 weeks)

#### Week 11-12: Query Parser Updates
- [ ] **Week 11:** Parser enhancements
  - Implement unique and prefer parameter parsing
  - Add new AST nodes for display options
  - Update Query class to handle display preferences
  - Unit tests for parser changes

- [ ] **Week 12:** SQL generation updates  
  - Implement unique:cards/art/prints SQL generation
  - Add prefer:newest/oldest logic with proper sorting
  - Optimize SQL queries for performance
  - Test SQL generation with various query combinations

#### Week 13-14: API Endpoint Changes
- [ ] **Week 13:** API response modifications
  - Update search endpoint to support new parameters
  - Modify response format to include display options
  - Implement backward compatibility for existing clients
  - Add caching support for different unique modes

- [ ] **Week 14:** Performance optimization
  - Implement query result caching by unique/prefer mode
  - Add database query optimization hints
  - Connection pool tuning for increased load
  - Memory usage optimization for larger result sets

#### Week 15: Integration Testing
- [ ] **Week 15:** End-to-end API testing
  - Integration tests for all unique/prefer combinations
  - Performance testing under realistic load
  - Cache performance validation
  - API response time benchmarking

### Phase 4: Testing and Validation (3 weeks)

#### Week 16: Comprehensive Testing
- [ ] **Week 16:** Test suite updates and execution
  - Update all existing tests for new data model
  - Add comprehensive tests for unique/prefer functionality
  - Performance regression testing
  - Load testing with production-like data volume

#### Week 17: Performance Validation
- [ ] **Week 17:** Performance benchmarking and optimization
  - Validate sub-50ms search query SLA compliance
  - Optimize slow queries identified in testing
  - Cache hit rate analysis and tuning
  - Database performance monitoring setup

#### Week 18: User Acceptance and Rollout
- [ ] **Week 18:** Final validation and rollout preparation
  - User acceptance testing with key stakeholders
  - Final performance validation against SLAs
  - Production deployment preparation
  - Documentation and training material completion

## Critical Dependencies

### External Dependencies
- **Scryfall API availability:** Required for bulk data downloads
- **Database maintenance windows:** For production migration
- **DevOps support:** For infrastructure scaling and monitoring

### Internal Dependencies
- **Current schema stability:** No conflicting schema changes during migration
- **API backward compatibility:** Existing clients must continue working
- **Performance baseline:** Need current performance metrics for comparison

### Technical Prerequisites
- **Database storage expansion:** Additional 8GB storage provisioned
- **Memory allocation:** Increased cache memory to 2GB
- **Monitoring infrastructure:** Enhanced monitoring for new metrics

## Risk Mitigation Schedule

### High-Risk Milestones
- **Week 6:** Production database migration
  - Risk: Data corruption or extended downtime
  - Mitigation: Comprehensive testing, rollback procedures, maintenance window

- **Week 9:** First full re-ingestion with all printings
  - Risk: Memory issues or import failures with 95k+ records
  - Mitigation: Batch processing, memory monitoring, staged rollout

- **Week 15:** API performance validation
  - Risk: Performance degradation beyond acceptable limits
  - Mitigation: Performance testing, query optimization, caching strategies

### Risk Monitoring Points
- **Every 2 weeks:** Performance regression testing
- **Weekly:** Data integrity validation during migration phase
- **Daily:** Memory usage and query performance monitoring

## Success Metrics

### Performance Targets
- **Search query execution:** < 50ms (P95)
- **HTTP request completion:** < 70ms (P95)
- **Cache hit rate:** > 80%
- **Database storage:** < 10GB total

### Functional Requirements
- **unique:cards** returns one printing per Oracle card identity
- **unique:art** returns one card per unique artwork
- **unique:prints** returns all available printings
- **prefer:newest/oldest** correctly orders results by release date
- **Backward compatibility** maintained for existing API clients

### Quality Metrics
- **Zero data loss** during migration
- **Zero breaking changes** for existing API consumers  
- **>99.9% uptime** during migration and rollout
- **All existing tests passing** after changes

### Milestone Gates
Each phase requires sign-off on:
- All planned deliverables completed
- Performance targets met
- No critical bugs or data integrity issues
- Rollback procedures tested and verified

This timeline provides a structured approach to implementing all printings support while managing risks and ensuring system reliability.