# Sample Scryfall Cards Reference

This document provides reference examples of Magic: The Gathering cards and their corresponding Scryfall API data. These samples are useful for understanding the card data structure and testing the Scryfall OS implementation.

## Sample Cards Overview

The following four iconic Magic cards have been selected as reference examples, representing different card types and gameplay mechanics:

### 1. Lightning Bolt

- **Mana Cost**: {R} (1 mana)
- **Type**: Instant
- **Oracle Text**: Lightning Bolt deals 3 damage to any target.
- **Significance**: One of the most efficient damage spells in Magic, often used as a baseline for measuring card power level.
- **JSON Data**: [`lightning_bolt.json`](sample_data/lightning_bolt.json)

### 2. Black Lotus

- **Mana Cost**: {0} (0 mana)
- **Type**: Artifact
- **Oracle Text**: {T}, Sacrifice this artifact: Add three mana of any one color.
- **Significance**: The most famous and powerful card in Magic history, providing explosive mana acceleration. Part of the "Power Nine" and banned in most formats.
- **JSON Data**: [`black_lotus.json`](sample_data/black_lotus.json)

### 3. Sol Ring

- **Mana Cost**: {1} (1 mana)
- **Type**: Artifact
- **Oracle Text**: {T}: Add {C}{C}.
- **Significance**: A staple mana acceleration artifact that provides significant mana advantage. Legal and popular in Commander format.
- **JSON Data**: [`sol_ring.json`](sample_data/sol_ring.json)

### 4. Brainstorm

- **Mana Cost**: {U} (1 mana)
- **Type**: Instant
- **Oracle Text**: Draw three cards, then put two cards from your hand on top of your library in any order.
- **Significance**: A powerful card selection and deck manipulation spell, highly valued in competitive formats.
- **JSON Data**: [`brainstorm.json`](sample_data/brainstorm.json)

## Sample Data Structure

Each JSON file contains the complete Scryfall API response for the card, including:

- **Basic Information**: Name, mana cost, converted mana cost (CMC), type line
- **Game Data**: Oracle text, power/toughness (for creatures), loyalty (for planeswalkers)
- **Identifiers**: Scryfall ID, Oracle ID, Multiverse IDs
- **Images**: URLs for different image sizes and crops
- **Legality**: Format legality information
- **Printing Information**: Set, rarity, collector number, release date
- **Market Data**: Pricing information from various sources
- **Metadata**: Colors, color identity, keywords, etc.

## Usage in Development

These sample cards can be used for:

1. **Testing Parser Functionality**: Use card names and attributes in search queries
2. **Database Schema Validation**: Ensure all card fields are properly handled
3. **API Response Format Verification**: Compare local API responses with official Scryfall data
4. **Image Handling**: Test image URL processing and display
5. **Legality System Testing**: Verify format legality tracking

## API Endpoints Referenced

The original JSON data was retrieved from these Scryfall API endpoints:

- Lightning Bolt: `https://api.scryfall.com/cards/77c6fa74-5543-42ac-9ead-0e890b188e99`
- Black Lotus: `https://api.scryfall.com/cards/bd8fa327-dd41-4737-8f19-2cf5eb1f7cdd`
- Sol Ring: `https://api.scryfall.com/cards/c946b161-0e4f-4c0a-a075-cdcf05504d0b`
- Brainstorm: `https://api.scryfall.com/cards/6fcf5f9e-74b3-43cb-8f5a-aa564f5e7acb`

## Notes

- All sample data is in JSON format with pretty-printing enabled
- Cards represent different power levels and formats to provide diverse testing scenarios
- The data includes the complete Scryfall response structure for comprehensive testing
- These specific printings were chosen as representative examples; other printings of these cards exist with different IDs

For more information about the Scryfall API format, see the [official Scryfall API documentation](https://scryfall.com/docs/api).
