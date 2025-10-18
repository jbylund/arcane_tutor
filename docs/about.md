# About Arcane Tutor

## Project Mission

Arcane Tutor is an open-source Magic: The Gathering card search engine designed to provide a fast, powerful, and transparent alternative for the MTG community. Our goal is to create a feature-rich search tool that respects intellectual property rights while offering unique capabilities and complete openness.

## Why Arcane Tutor Exists

### Open Source Philosophy

We believe in:
- **Transparency**: All code is open and available for review
- **Community ownership**: Anyone can contribute, fork, or self-host
- **Educational value**: Learn how card search engines work
- **Data sovereignty**: Run your own instance with your own data

### Unique Features

Arcane Tutor offers capabilities beyond traditional card search:

1. **Arithmetic Expressions**: Search with math like `cmc+1<power` or `power-toughness=0`
2. **No Pagination Limits**: Fetch more results than typical 175 card/page limits
3. **Performance Optimizations**: Custom PostgreSQL schema for fast queries
4. **Local Deployment**: Run your own instance via Docker
5. **Extensibility**: Open source means you can add your own features

### Alternative Implementation

While we use Scryfall's excellent card data (with attribution and compliance), Arcane Tutor is a completely independent implementation:
- All code written from scratch
- Original query parser and search algorithms
- Custom database schema
- Different UI/UX approach
- Unique feature set

## How We Differ from Scryfall

Arcane Tutor is not a clone of Scryfall. Here's how we're different:

### Technical Differences
- **Original Codebase**: 100% original code, no copied implementation
- **Independent Database**: Custom PostgreSQL schema optimized for our use cases
- **Different Architecture**: Falcon-based API with bjoern WSGI server
- **Unique Parser**: Custom pyparsing-based query DSL implementation
- **Extended Features**: Arithmetic expressions and other unique capabilities

### Visual Design
- **Different Color Scheme**: Blue gradient theme (Tolarian Academy inspired)
- **Original Layout**: Custom card grid and modal display
- **Unique UI Components**: Original search controls and dropdowns
- **Different Typography**: Custom font choices (Beleren, MPlantin)

### Philosophy
- **Open Source First**: Complete transparency and community ownership
- **Self-Hostable**: Run your own instance with Docker
- **No Limits**: Designed for power users who need more data
- **Extensible**: Fork and customize for your needs

## Technology Stack

- **Backend**: Python 3.13+ with Falcon web framework
- **Database**: PostgreSQL 17+ with optimized indexing
- **Web Server**: bjoern WSGI server for performance
- **Frontend**: Single-page HTML/JavaScript application
- **Deployment**: Docker and Docker Compose support

## Data Sources & Attribution

### Card Data
We use card data from **Scryfall's official bulk data API** with proper attribution:
- Data Provider: [Scryfall](https://scryfall.com)
- Usage: Bulk data exports for card information
- Compliance: We follow Scryfall's API Terms of Service

### Card Images
Card images are processed from Scryfall's images and served via our own S3/CloudFront infrastructure:
- Source: Derived from Scryfall's PNG images
- Storage: Amazon S3 bucket
- Delivery: CloudFront CDN at `d1hot9ps2xugbc.cloudfront.net`
- Rights: All card artwork © Wizards of the Coast LLC

### Intellectual Property
All Magic: The Gathering card names, artwork, and game content are:
- © Wizards of the Coast LLC
- Used under [Wizards' Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy)
- Not approved/endorsed by Wizards of the Coast

**Arcane Tutor is unofficial Fan Content permitted under the Fan Content Policy.**

## Project Status

### Current Features ✅
- Complete search syntax support (matching Scryfall)
- Arithmetic expressions in queries
- Optimized database performance
- Docker deployment support
- Card tagging system
- Multiple sort and display options
- Light/dark theme support

### In Development 🚧
- Double-faced card support improvements
- Comprehensive tagging features
- Additional search operators (`cube:`, `papersets:`)
- Enhanced documentation

## Legal & Compliance

We take compliance seriously:

- ✅ **Original Code**: All code written from scratch
- ✅ **Proper Attribution**: Clear acknowledgment of Wizards and Scryfall
- ✅ **Fan Content Policy**: Operating within Wizards' guidelines
- ✅ **Terms of Service**: Documented in [docs/TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md)
- ✅ **Privacy Policy**: Documented in [docs/PRIVACY_POLICY.md](PRIVACY_POLICY.md)
- ✅ **Legal Documentation**: Comprehensive [docs/LEGAL.md](LEGAL.md)

See [docs/COMPLIANCE_REVIEW.md](COMPLIANCE_REVIEW.md) for detailed compliance status.

## Contributing

Arcane Tutor is community-driven and welcomes contributions:

- **Report Issues**: [GitHub Issues](https://github.com/jbylund/arcane_tutor/issues)
- **Submit Pull Requests**: Code improvements and bug fixes
- **Documentation**: Help improve guides and examples
- **Testing**: Add test cases and validation

See [README.md](../README.md) for developer setup instructions.

## Acknowledgments

### Scryfall
We are deeply grateful to [Scryfall](https://scryfall.com) for:
- Maintaining comprehensive card data
- Providing public bulk data APIs
- Supporting the Magic community
- Setting the standard for card search

**Arcane Tutor is not affiliated with, endorsed by, or sponsored by Scryfall.** We are an independent open-source implementation.

### Wizards of the Coast
Magic: The Gathering™ is a trademark of Wizards of the Coast LLC. We thank Wizards for:
- Creating the amazing game of Magic
- Supporting fan content through their Fan Content Policy
- Maintaining game data and rulings

### Open Source Community
Built with excellent open-source tools:
- Python, PostgreSQL, Falcon
- pyparsing for DSL parsing
- Docker for deployment
- And many other libraries (see requirements/)

## Contact & Support

- **GitHub Repository**: [github.com/jbylund/arcane_tutor](https://github.com/jbylund/arcane_tutor)
- **Issues & Bugs**: [GitHub Issues](https://github.com/jbylund/arcane_tutor/issues)
- **Documentation**: See [docs/](.) directory
- **Legal Inquiries**: Open an issue or contact repository owner

## License

Arcane Tutor code is licensed under the ISC License (see package.json).

**Note**: This license applies only to our original code and does not grant rights to:
- Wizards of the Coast's intellectual property
- Scryfall's data or branding
- Third-party fonts or assets

## Future Vision

We aim to:
- Continue expanding search capabilities
- Improve performance and user experience
- Add more unique features not found elsewhere
- Maintain strong compliance and attribution
- Grow the community of contributors
- Support self-hosted deployments

---

**Arcane Tutor**: An open-source Magic: The Gathering card search engine by the community, for the community.

*Last Updated: October 2025*
