# Scryfall OS Functionality Analysis

## Executive Summary

This document provides a comprehensive analysis of Scryfall search functionality and compares the current Scryfall OS implementation against the official Scryfall API. The analysis reveals significant gaps in functionality that should be prioritized for development.

## Methodology

1. **Functionality Mapping**: Analyzed official Scryfall syntax documentation and current codebase
2. **API Comparison**: Automated testing comparing official Scryfall API vs local implementation
3. **Gap Analysis**: Identified missing or incomplete features

## Current Implementation Status

### ✅ Fully Supported Features

Based on the codebase analysis in `api/parsing/db_info.py` and successful API comparisons:

1. **Basic Search**
   - `name:` - Card name searches
   - `oracle:` or `o:` - Oracle text searches  
   - `type:` or `t:` - Type line searches

2. **Numeric Attributes**
   - `cmc:` - Converted mana cost
   - `power:` or `pow:` - Creature power
   - `toughness:` or `tou:` - Creature toughness

3. **Colors and Identity**  
   - `color:` or `c:` - Card colors (JSONB object)
   - `identity:` or `id:` - Color identity (JSONB object)

4. **Advanced Features**
   - `keywords:` or `k:` - Keyword abilities (JSONB object)
   - `oracle_tags:` or `ot:` - Oracle tags (Scryfall OS extension)

5. **Operators**
   - Comparison: `=`, `<`, `>`, `<=`, `>=`, `!=`, `<>`
   - Logic: `AND`, `OR`, `NOT`, `-` (negation)
   - Arithmetic: `+`, `-`, `*`, `/` (e.g., `cmc+1<power`)
   - Grouping: `()` parentheses

### ⚠️ Partially Supported Features

1. **Card Types** 
   - `subtypes:` - Implemented as JSONB array
   - Status: Works but may have data completeness issues

2. **Mana Costs**
   - `mana:` - Both JSONB object and text representations available
   - Status: Implementation exists but comparison shows discrepancies

### ❌ Missing Critical Features

Based on API comparison failures and official Scryfall documentation:

#### High Priority Missing Features

1. **Set and Collection Data**
   - `set:` or `s:` - Set codes
   - `edition:` or `e:` - Set names
   - `number:` or `cn:` - Collector numbers
   - `rarity:` or `r:` - Card rarity

2. **Format Legality**
   - `format:` or `f:` - Format legality
   - `legal:` - Legal in specific format
   - `banned:` - Banned in specific format
   - `restricted:` - Restricted in specific format

3. **Pricing Data**
   - `usd:` - USD prices
   - `eur:` - EUR prices  
   - `tix:` - MTGO ticket prices

4. **Card Properties**
   - `layout:` - Card layouts (normal, split, flip, etc.)
   - `border:` - Border colors
   - `frame:` - Frame versions
   - `artist:` or `a:` - Artist names
   - `flavor:` - Flavor text

5. **Special Properties**
   - `is:` - Special card properties (permanent, spell, historic, etc.)
   - `produces:` - Mana production
   - `watermark:` - Watermarks

#### Medium Priority Missing Features

1. **Dates and Releases**
   - `year:` - Release year
   - `date:` - Specific release dates

2. **Advanced Mechanics**
   - `loyalty:` - Planeswalker loyalty
   - `spellpower:` - Spell power (Alchemy)
   - `spellresistance:` - Spell resistance (Alchemy)
   - `devotion:` - Mana symbol devotion

3. **Collection Features**
   - `cube:` - Cube inclusion
   - `commander:` or `cmd:` - Commander-related
   - `papersets:` - Paper set inclusion

#### Low Priority Advanced Features

1. **Complex Search**
   - Regular expressions: `/pattern/`
   - Functions: `max:power`, `min:cmc`
   - Wildcards: `*` for partial matches

2. **Meta Features**
   - `is:booster` - Available in boosters
   - `is:spotlight` - Spotlight cards
   - Various timeshifted properties

## API Comparison Results

### Test Results Summary (21 queries tested)
- **Official API success rate**: 100% (21/21)
- **Local API success rate**: 100% (21/21) 
- **Major discrepancies**: 4.8% (1/21)

### Key Findings

1. **Server Stability Resolved**
   - Local API (scryfall.crestcourt.com) now running reliably
   - No more 502 Bad Gateway errors during testing

2. **Significant Improvement in Results**
   - Both APIs now achieving 100% success rates
   - Major discrepancies reduced from 100% to 4.8% of queries
   - Position correlation dramatically improved (0.98-1.00 for most queries)

3. **Remaining Data Quality Issues**
   - Small result count differences (typically 1-15 cards)
   - One major discrepancy with `keyword:flying` query (295 vs 0 results)
   - Minor variations in card ordering and availability

## Recommendations

### Immediate Priorities (Address Remaining Issues)

1. **Investigate Data Discrepancies**
   - Analyze small result count differences (1-15 cards typically)
   - Investigate the `keyword:flying` major discrepancy (295 vs 0 results)
   - Ensure card database completeness and currency

2. **Data Source Analysis**
   - Consider migrating from `oracle_cards` to `default_cards` bulk data
   - Evaluate impact on data completeness and search accuracy
   - Plan for handling non-unique card names in new data model

3. **Query Parsing Alignment**
   - Review official Scryfall syntax for `k:`/`keywords:` support
   - Standardize parsing behavior with official implementation

### High Priority Development (Core Missing Features)

1. **Set and Rarity Data**
   - Add database schema for sets, collector numbers, rarities
   - Implement parsing for `set:`, `rarity:`, `number:` syntax

2. **Format Legality System**
   - Add legality tracking for major formats
   - Implement `format:`, `legal:`, `banned:` syntax

3. **Pricing Integration**
   - Add price tracking capabilities
   - Implement `usd:`, `eur:`, `tix:` syntax

### Medium Priority Development

1. **Card Metadata Expansion**
   - Artist, flavor text, layout information
   - Border, frame, watermark data

2. **Advanced Search Features**
   - `is:` property syntax
   - `produces:` mana production
   - Date-based searches

### Testing and Quality Assurance

1. **Automated Comparison Suite**
   - Expand test query coverage
   - Implement regression testing
   - Add performance benchmarking

2. **Data Quality Monitoring**
   - Regular comparison with official API
   - Alert on major discrepancies
   - Track feature completion rates

## Conclusion

The Scryfall OS project has a solid foundation with core text search, numeric comparisons, and color/identity features working. However, significant gaps remain in set data, format legality, pricing, and card metadata that limit its usefulness as a complete Scryfall replacement.

The automated comparison script provides a framework for ongoing quality assurance and feature development prioritization. Focus should be on server stability, data completeness, and implementing the most commonly used missing features first.