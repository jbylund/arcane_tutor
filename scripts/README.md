# Scripts Directory

This directory contains utility scripts for the Scryfall OS project.

## Take Screenshot Script

### Overview
`take_screenshot.py` is a complete automation script that:
1. Starts the API server
2. Loads sample cards into the database
3. Gets the public IP address of the server
4. Makes a request to screenshotmachine.com to get a screenshot of the rendered page

### Usage

#### Basic Usage
```bash
python scripts/take_screenshot.py
```

This will:
- Start the server on port 8080 with 2 workers
- Wait for the server to be ready
- Load sample cards automatically
- Get the public IP address
- Take a screenshot with default parameters (query: "t:beast", orderby: "edhrec", direction: "asc")
- Save the screenshot to a timestamped PNG file

#### Requirements
- All dependencies from `requirements.txt` and `test-requirements.txt`
- Internet access for myip.wtf and screenshotmachine.com APIs
- Available port 8080 (or modify the script for different port)

#### Output
The script will:
- Display progress in the console with timestamps
- Save the screenshot as `screenshot_<timestamp>.png`
- Display the final results including screenshot URL and file details
- Automatically clean up the server process when done

#### Example Output
```
2025-09-24 01:45:51,228 - INFO - Starting screenshot script...
2025-09-24 01:45:51,228 - INFO - Starting API server on port 8080...
2025-09-24 01:45:51,229 - INFO - Server process started with PID 2730
2025-09-24 01:45:51,229 - INFO - Waiting for server to be ready...
2025-09-24 01:45:55,234 - INFO - Server is ready!
2025-09-24 01:45:55,234 - INFO - Loading sample cards...
2025-09-24 01:46:20,445 - INFO - Sample cards loaded successfully
2025-09-24 01:46:20,445 - INFO - Getting public IP address...
2025-09-24 01:46:21,123 - INFO - Public IP address: 172.182.226.135
2025-09-24 01:46:21,123 - INFO - Taking screenshot...
2025-09-24 01:46:21,123 - INFO - Target URL: https://172.182.226.135:8080/?q%3Dt%253Abeast%26orderby%3Dedhrec%26direction%3Dasc
2025-09-24 01:46:25,789 - INFO - Screenshot taken successfully: 52690 bytes, content-type: image/png
2025-09-24 01:46:25,790 - INFO - Screenshot saved to: screenshot_1727139985.png
2025-09-24 01:46:25,790 - INFO - Screenshot process completed successfully!
```

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
- Keywords (`keyword:flying`)
- Oracle tags (`otag:haste`) - Scryfall OS extension
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