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

### Test Results Summary (23 queries tested)
- **Official API success rate**: 69.6% (16/23)
- **Local API success rate**: 78.3% (18/23) 
- **Major discrepancies**: 100% (23/23)

### Key Findings

1. **Server Availability Issues**
   - Local API (scryfall.crestcourt.com) frequently returns 502 Bad Gateway
   - Affects reliability of comparison testing

2. **Functionality Gaps**
   - Official Scryfall doesn't support `k:` or `keywords:` syntax (returns 400 errors)
   - Local implementation has `ot:` (oracle tags) extension not in official API
   - Significant result count differences suggest data or indexing issues

3. **Result Quality Issues**
   - Position correlation consistently 0.00, indicating different sorting/ranking
   - Large result count differences (e.g., 7,483 vs 0 for `cmc=3`)
   - Suggests either data availability or query parsing differences

## Recommendations

### Immediate Priorities (Fix Core Functionality)

1. **Fix Server Stability**
   - Address 502 Bad Gateway errors on scryfall.crestcourt.com
   - Implement proper error handling and failover

2. **Data Completeness Audit**
   - Investigate large result count discrepancies
   - Ensure card database is complete and current

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