# Screenshot Generation Script

This directory contains the script used to generate the README screenshot for Scryfall OS.

## Overview

The `take_screenshot.sh` script automates the entire process of setting up the development environment, starting the server, importing sample cards, and capturing a screenshot of the dark mode interface.

## Quick Start

```bash
# Run with defaults
./scripts/take_screenshot.sh

# Run with custom options
./scripts/take_screenshot.sh --port 9000 --workers 4 --output /tmp/my-screenshot.png
```

## Requirements

### System Dependencies
- **Python 3.x** - For running the API server
- **Google Chrome** - For taking screenshots with proper rendering
- **curl** - For API calls and server health checks
- **libev-dev** (optional) - For webserver dependencies

### Installation on Ubuntu/Debian
```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3 python3-venv curl libev-dev

# Install Google Chrome
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt-get update
sudo apt-get install -y google-chrome-stable
```

## Script Workflow

The script follows this procedure:

1. **Dependency Check** - Verifies required tools are installed
2. **Environment Setup** - Creates Python virtual environment and installs packages
3. **Server Start** - Launches the Scryfall OS API server with testcontainers
4. **Card Import** - Imports sample Magic cards for demonstration:
   - Lightning Bolt
   - Black Lotus
   - Path to Exile
   - Llanowar Elves
   - Brainstorm
   - Sol Ring
5. **Search Verification** - Confirms the search functionality works
6. **Screenshot Capture** - Uses Chrome with dark mode flags to capture the interface
7. **Cleanup** - Stops server and cleans up temporary files

## Usage Options

```bash
Usage: ./scripts/take_screenshot.sh [OPTIONS]

Options:
    -p, --port PORT         Server port (default: 8080)
    -w, --workers WORKERS   Number of workers (default: 2)
    -o, --output PATH       Screenshot output path (default: ./scryfallos-screenshot.png)
    -t, --timeout SECONDS   Screenshot timeout (default: 120)
    -h, --help             Show help message

Environment Variables:
    PORT                   Server port
    WORKERS                Number of workers  
    SCREENSHOT_PATH        Screenshot output path
    TIMEOUT_SECONDS        Screenshot timeout
```

## Examples

```bash
# Basic usage - generates scryfallos-screenshot.png in current directory
./scripts/take_screenshot.sh

# Custom port and workers
./scripts/take_screenshot.sh --port 9000 --workers 4

# Custom output location
./scripts/take_screenshot.sh --output /tmp/my-screenshot.png

# Using environment variables
PORT=9000 WORKERS=4 SCREENSHOT_PATH=/tmp/screenshot.png ./scripts/take_screenshot.sh

# With timeout for slower systems
./scripts/take_screenshot.sh --timeout 180
```

## Chrome Configuration

The script uses Chrome with these specific flags for optimal screenshot capture:

- `--headless=new` - Modern headless mode
- `--force-dark-mode` - Forces dark mode theme
- `--enable-features=WebUIDarkMode` - Enables dark mode features
- `--disable-web-security` - Allows external resource loading
- `--window-size=1400,1200` - Sets consistent window size
- `--virtual-time-budget=15000` - Ensures complete page loading

## Troubleshooting

### Common Issues

**Script fails with "Missing dependencies"**
```bash
# Install missing system packages
sudo apt-get install -y python3 python3-venv curl google-chrome-stable
```

**Server fails to start**
```bash
# Check if port is already in use
sudo lsof -i :8080

# Try a different port
./scripts/take_screenshot.sh --port 9000
```

**Screenshot is blank or white**
- Ensure Chrome is properly installed
- Try increasing the timeout: `--timeout 180`
- Check server logs for errors

**bjoern compilation fails**
```bash
# Install libev development headers
sudo apt-get install -y libev-dev
```

### Debug Mode

For debugging, you can run parts of the script manually:

```bash
# Set up environment only
source venv/bin/activate
python -m pip install uv
uv pip install -r requirements.txt -r test-requirements.txt

# Start server manually
python -m api.entrypoint --port 8080 --workers 2 &

# Test server
curl http://localhost:8080/

# Import cards manually
curl "http://localhost:8080/import_cards_by_search?search_query=name:%22Lightning%20Bolt%22"

# Take screenshot manually (adjust paths as needed)
google-chrome --headless=new --screenshot=/tmp/test.png http://localhost:8080/
```

## Output

The script generates a screenshot that shows:
- Scryfall OS web interface in dark mode
- Search results for "cmc<10" (cards with converted mana cost less than 10)
- All imported sample cards with their details
- Dark theme with proper purple/gray styling
- Search completed with timing information

## Integration

This script is used to maintain the README screenshot. To update the main screenshot:

```bash
# Generate new screenshot
./scripts/take_screenshot.sh

# The screenshot is saved as scryfallos-screenshot.png
# Commit the updated file
git add scryfallos-screenshot.png
git commit -m "Update README screenshot"
```

## Technical Details

### Chrome Flags Explanation

- `--force-dark-mode` - Forces all websites to render in dark mode
- `--enable-features=WebUIDarkMode` - Enables Chrome's dark mode UI features
- `--disable-web-security` - Disables same-origin policy to allow card image loading
- `--virtual-time-budget=15000` - Gives the page 15 seconds of virtual time to fully load
- `--run-all-compositor-stages-before-draw` - Ensures complete rendering before screenshot

### Card Import Process

The script imports specific cards that demonstrate various aspects of the application:
- **Lightning Bolt** - Common instant, low CMC
- **Black Lotus** - Powerful artifact, 0 CMC  
- **Path to Exile** - Common removal spell
- **Llanowar Elves** - Creature, mana dork
- **Brainstorm** - Card draw spell
- **Sol Ring** - Popular artifact

These cards provide a good variety of card types and demonstrate the search and display functionality.