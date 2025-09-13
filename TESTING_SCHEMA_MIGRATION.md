# Testing Schema Migration for Multiple Tag Parents

This document outlines how to test the card_tags schema migration that allows tags to have multiple parents.

## Problem Fixed

Previously, the `cost-reducer-artifact` tag could only have one parent due to a `PRIMARY KEY (tag)` constraint. After the fix, it can have both `synergy-artifact` and `cost-reducer` as parents.

## Migration File

`api/db/2025-09-13-fix-card-tags-pkey.sql` contains the schema migration that:

1. Drops the existing `PRIMARY KEY (tag)` constraint
2. Adds new `PRIMARY KEY (tag, parent_tag)` constraint
3. Adds unique constraint for root tags (where `parent_tag IS NULL`)

## Manual Testing with Docker

To test the migration with a real PostgreSQL database:

1. Start the database:
   ```bash
   make datadir
   make build_images
   make up
   ```

2. Connect to the database:
   ```bash
   make dbconn
   ```

3. Apply the migration:
   ```sql
   \i api/db/2025-09-13-fix-card-tags-pkey.sql
   ```

4. Test multiple parents:
   ```sql
   -- Insert test data
   INSERT INTO magic.card_tags (tag, parent_tag) VALUES 
   ('synergy-artifact', NULL),
   ('cost-reducer', NULL),
   ('cost-reducer-artifact', 'synergy-artifact'),
   ('cost-reducer-artifact', 'cost-reducer');

   -- Verify multiple parents work
   SELECT * FROM magic.card_tags WHERE tag = 'cost-reducer-artifact';
   ```

   Expected result:
   ```
             tag          |    parent_tag    
   -----------------------+------------------
    cost-reducer-artifact | synergy-artifact
    cost-reducer-artifact | cost-reducer
   (2 rows)
   ```

## Integration Testing

For full integration testing, consider implementing testcontainers-based tests that:

- Start a PostgreSQL container
- Apply all schema migrations in order
- Test tag import functionality
- Verify search functionality against real database

This would require adding `testcontainers` to `test-requirements.txt` and creating comprehensive database integration tests.

## Current Test Coverage

The current tests in `api/tests/test_card_tags_schema.py` provide conceptual validation of the logic changes. For production deployment, run the migration during a maintenance window and verify the `_populate_tag_hierarchy` method works correctly.