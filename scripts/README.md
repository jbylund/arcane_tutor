# Scripts Directory

This directory contains utility scripts for the Scryfall OS project.

## Scryfall Comparison Script

### Overview
`scryfall_comparison_script.py` compares search results between the official Scryfall API and the local Scryfall OS implementation to identify functionality gaps and data discrepancies.

### Usage

#### Full Comparison Suite
```bash
python scripts/scryfall_comparison_script.py
```

This runs 23 test queries covering various search features and generates a detailed report.

#### Programmatic Usage
```python
from scripts.scryfall_comparison_script import ScryfallAPIComparator

comparator = ScryfallAPIComparator()
result = comparator.compare_results("cmc=3")

print(f"Official: {result.official_result.total_cards} cards")
print(f"Local: {result.local_result.total_cards} cards")
print(f"Correlation: {result.position_correlation:.2f}")
```

### Output

The script generates:
1. **Console output** - Real-time progress and summary
2. **Report file** - Detailed markdown report saved to `/tmp/scryfall_comparison_report.md`

### Key Metrics

- **Result Count Difference** - Absolute difference in number of results
- **Position Correlation** - How similarly results are ordered (0.0 = no correlation, 1.0 = identical)
- **Major Discrepancy** - Flags when APIs differ significantly or fail

### Test Queries

The script tests various functionality including:
- Basic text search (`lightning`)
- Type searches (`t:beast`)
- Color searches (`c:g`, `id:g`)
- Numeric comparisons (`cmc=3`, `power>3`)
- Complex queries (`t:beast id:g`)
- Keywords (`k:flying`)
- Oracle tags (`ot:haste`) - Scryfall OS extension
- Arithmetic (`cmc+1<power`)
- Edge cases and error conditions

### Dependencies

- `requests` - HTTP client
- `dataclasses` - Data structures
- Python 3.7+ - Type hints and dataclasses

### Rate Limiting

The script includes automatic rate limiting (0.1-0.2 second delays) to be respectful to API endpoints.

### Error Handling

- Handles network timeouts and connection errors
- Graceful handling of 404 (no results) and 400 (bad query) responses
- Special handling for 502 errors from local API server downtime