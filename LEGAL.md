# Legal Documentation and Compliance

## Overview

Scryfall OS is an independent, open-source implementation of a Magic: The Gathering card search engine. This document outlines our legal compliance approach, data sources, and relationship to related properties.

## Relationship to Official Properties

### Not Affiliated with Scryfall

Scryfall OS is **not affiliated with, endorsed by, or sponsored by Scryfall LLC**. While we admire Scryfall's work and have implemented similar search functionality, we are an independent project with:

- Original codebase developed from scratch
- Independent database schema design
- Original parsing algorithms and search implementation
- Distinct visual design and user interface
- Different feature set and unique capabilities

The name "Scryfall OS" indicates this is an "open source" implementation of card search functionality, not an official Scryfall product.

### Not Affiliated with Wizards of the Coast

Scryfall OS is **not affiliated with, endorsed by, sponsored by, or officially connected with Wizards of the Coast LLC** or any of its subsidiaries or affiliates.

## Compliance with Wizards of the Coast Fan Content Policy

This project complies with the [Wizards of the Coast Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy). Specifically:

### What We Do

1. **Non-Commercial Use**: This is an open-source project made available for free
2. **Proper Attribution**: We clearly indicate that Magic: The Gathering is property of Wizards of the Coast
3. **No Misrepresentation**: We do not claim to be official or endorsed by Wizards
4. **Trademark Usage**: We follow proper trademark usage guidelines
5. **Original Content**: All code, algorithms, and UI design are original works

### Data Sources

#### Card Data

We obtain Magic: The Gathering card data from **official Wizards of the Coast sources**:

1. **Scryfall Bulk Data API**: We use Scryfall's publicly available bulk data exports, which compile official Wizards data. This usage complies with [Scryfall's API Terms](https://scryfall.com/docs/api).
2. **Official Gatherer**: When appropriate, we reference data from Wizards' official Gatherer database.

**Important**: We do not scrape Scryfall's website. We use their officially provided bulk data API, which is made available for projects like ours.

#### Card Images

Card images are sourced from:
- Official Wizards of the Coast image servers where available
- Scryfall's image API in accordance with their terms of service
- Properly attributed and used in compliance with fan content policies

#### Pricing Data

Price information is obtained from third-party market data sources and Scryfall's bulk data, which aggregates market prices from various retailers.

### Attribution Requirements

When using Scryfall's bulk data API, we acknowledge:

> This project uses data provided by [Scryfall LLC](https://scryfall.com/). Portions of Scryfall are unofficial Fan Content permitted under the Wizards of the Coast Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast LLC. © Wizards of the Coast LLC.

## Intellectual Property Acknowledgments

### Wizards of the Coast

Magic: The Gathering, the mana symbols, the tap symbol, and all other elements of the Magic game are trademarks and copyrights of Wizards of the Coast LLC, a subsidiary of Hasbro, Inc.

**Full Legal Notice**:

> Wizards of the Coast, Magic: The Gathering, and their logos are trademarks of Wizards of the Coast LLC in the United States and other countries. © 1993-2025 Wizards. All Rights Reserved.
>
> Scryfall OS is not affiliated with, endorsed, sponsored, or specifically approved by Wizards of the Coast LLC. Scryfall OS may use the trademarks and other intellectual property of Wizards of the Coast LLC, which is permitted under Wizards' Fan Content Policy. MAGIC: THE GATHERING® is a trademark of Wizards of the Coast. For more information about Wizards of the Coast or any of Wizards' trademarks or other intellectual property, please visit their website at www.wizards.com.

### Scryfall LLC

Scryfall and the Scryfall logo are trademarks of Scryfall LLC. We use the name "Scryfall OS" to indicate that this is an open-source implementation of similar search functionality, but we are not affiliated with or endorsed by Scryfall LLC.

We use Scryfall's bulk data API in accordance with their terms of service, which permits use of their data compilation for projects like ours.

## Font and Visual Assets

### Fonts Used

This project uses the following fonts:

1. **Beleren** - The official Magic: The Gathering font, used in compliance with fan content policies
2. **Mana Font** - For displaying mana symbols, used in accordance with community standards for MTG tools
3. **MPlantin** - Used for card text display

These fonts are used exclusively for displaying Magic: The Gathering related content in a manner consistent with fan content usage.

## Third-Party Libraries

This project uses various open-source libraries, each with their own licenses:

- **Python Libraries**: See `requirements/` directory for complete list
  - psycopg (PostgreSQL adapter) - LGPL
  - falcon (Web framework) - Apache 2.0
  - pyparsing (Parser library) - MIT
  - pytest (Testing) - MIT
  - ruff (Linting) - MIT
  
- **JavaScript Libraries**: Minimal dependencies, primarily vanilla JavaScript

All third-party libraries are used in compliance with their respective licenses.

## Project License

This project's original code is released under the [MIT License](LICENSE) (or similar open-source license as specified in the LICENSE file).

This license applies only to the original code written for this project, not to:
- Magic: The Gathering game content, trademarks, or copyrights (owned by Wizards of the Coast)
- Data obtained from Scryfall (subject to their terms)
- Third-party libraries (subject to their own licenses)

## User Privacy and Data Collection

See our [Privacy Policy](PRIVACY_POLICY.md) for details on how we handle user data.

**Summary**:
- We do not collect personal information
- We do not use tracking cookies
- We do not sell or share user data
- Search queries are not logged or stored
- This is a self-hosted application - deployment operators are responsible for their own data practices

## Contact and Legal Inquiries

For legal inquiries, licensing questions, or concerns about this project:

- **GitHub Issues**: https://github.com/jbylund/arcane_tutor/issues
- **Project Repository**: https://github.com/jbylund/arcane_tutor

If you represent Wizards of the Coast or Scryfall LLC and have concerns about this project, please contact us through the above channels.

## Compliance Updates

This legal documentation is maintained and updated as needed to ensure continued compliance with:
- Wizards of the Coast Fan Content Policy
- Scryfall API Terms of Service
- Open-source software licenses
- Applicable trademark and copyright law

**Last Updated**: October 2025

## Cease and Desist Procedure

If we receive any legal concerns or cease and desist notices:

1. We will immediately evaluate the concern
2. Take appropriate action to address legitimate issues
3. Modify or remove content as necessary to maintain compliance
4. Update this documentation to reflect any changes

We are committed to operating within legal boundaries and respecting all intellectual property rights.
