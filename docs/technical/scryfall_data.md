# Sample Scryfall Cards Reference

This document provides reference examples of Magic: The Gathering cards and their corresponding Scryfall API data.
These samples are useful for understanding the card data structure and testing the Scryfall OS implementation.

## Sample Cards Overview

The following nine iconic Magic cards have been selected as reference examples, providing complete coverage of all major card types and colors:

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

### 5. Plains

- **Mana Cost**: {0} (0 mana)
- **Type**: Basic Land — Plains
- **Oracle Text**: {T}: Add {W}.
- **Significance**: The fundamental white mana source, representing the most basic building block of Magic mana systems.
- **JSON Data**: [`plains.json`](sample_data/plains.json)

### 6. Llanowar Elves

- **Mana Cost**: {G} (1 mana)
- **Type**: Creature — Elf Druid
- **Power/Toughness**: 1/1
- **Oracle Text**: {T}: Add {G}.
- **Significance**: Iconic mana-producing creature, one of the most recognizable and reprinted cards in Magic history.
- **JSON Data**: [`llanowar_elves.json`](sample_data/llanowar_elves.json)

### 7. Serra Angel

- **Mana Cost**: {3}{W}{W} (5 mana)
- **Type**: Creature — Angel
- **Power/Toughness**: 4/4
- **Oracle Text**: Flying, vigilance
- **Significance**: Classic white creature and one of Magic's original "big" creatures, representing white's flying and protection themes.
- **JSON Data**: [`serra_angel.json`](sample_data/serra_angel.json)

### 8. Demonic Tutor

- **Mana Cost**: {1}{B} (2 mana)
- **Type**: Sorcery
- **Oracle Text**: Search your library for a card, put it into your hand, then shuffle.
- **Significance**: The quintessential tutor effect in Magic, allowing players to search for any card. Part of the vintage power level.
- **JSON Data**: [`demonic_tutor.json`](sample_data/demonic_tutor.json)

### 9. Necropotence

- **Mana Cost**: {B}{B}{B} (3 mana)
- **Type**: Enchantment
- **Oracle Text**: Skip your draw step. Whenever you discard a card, exile that card from your graveyard. Pay 1 life: Exile the top card of your library face down. Put that card into your hand at the beginning of your next end step.
- **Significance**: One of the most powerful card advantage engines ever printed, nicknamed "Necro" and central to many historic tournament decks.
- **JSON Data**: [`necropotence.json`](sample_data/necropotence.json)

## Complete Coverage

This collection provides comprehensive coverage for testing and development:

### Card Types

- **Artifact**: Black Lotus, Sol Ring
- **Instant**: Lightning Bolt, Brainstorm
- **Sorcery**: Demonic Tutor
- **Creature**: Llanowar Elves, Serra Angel
- **Enchantment**: Necropotence
- **Land**: Plains

### Colors

- **White (W)**: Serra Angel, Plains
- **Blue (U)**: Brainstorm
- **Black (B)**: Demonic Tutor, Necropotence
- **Red (R)**: Lightning Bolt
- **Green (G)**: Llanowar Elves
- **Colorless**: Black Lotus, Sol Ring

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
1. **Database Schema Validation**: Ensure all card fields are properly handled
1. **API Response Format Verification**: Compare local API responses with official Scryfall data
1. **Image Handling**: Test image URL processing and display
1. **Legality System Testing**: Verify format legality tracking

## API Endpoints Referenced

The original JSON data was retrieved from these Scryfall API endpoints:

- Lightning Bolt: `https://api.scryfall.com/cards/77c6fa74-5543-42ac-9ead-0e890b188e99`
- Black Lotus: `https://api.scryfall.com/cards/bd8fa327-dd41-4737-8f19-2cf5eb1f7cdd`
- Sol Ring: `https://api.scryfall.com/cards/c946b161-0e4f-4c0a-a075-cdcf05504d0b`
- Brainstorm: `https://api.scryfall.com/cards/6fcf5f9e-74b3-43cb-8f5a-aa564f5e7acb`
- Plains: `https://api.scryfall.com/cards/4069fb4a-8ee1-41ef-ab93-39a8cc58e0e5`
- Llanowar Elves: `https://api.scryfall.com/cards/6a0b230b-d391-4998-a3f7-7b158a0ec2cd`
- Serra Angel: `https://api.scryfall.com/cards/3cee9303-9d65-45a2-93d4-ef4aba59141b`
- Demonic Tutor: `https://api.scryfall.com/cards/a24b4cb6-cebb-428b-8654-74347a6a8d63`
- Necropotence: `https://api.scryfall.com/cards/c89c6895-b0f8-444a-9c89-c6b4fd027b3e`

## Notes

- All sample data is in JSON format with pretty-printing enabled
- Cards provide complete coverage of all major card types and all five Magic colors plus colorless
- The data includes the complete Scryfall response structure for comprehensive testing
- These specific printings were chosen as representative examples; other printings of these cards exist with different IDs
- The collection ranges from basic lands to tournament staples to vintage-power cards, enabling diverse testing scenarios

For more information about the Scryfall API format, see the [official Scryfall API documentation](https://scryfall.com/docs/api).
