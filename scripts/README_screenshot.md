# Screenshot Generation Script

This directory contains a script to automatically take screenshots of the Scryfall OS web interface.

## Script: `take_screenshot.sh`

### Purpose

Generates a screenshot of the Scryfall OS web interface in dark mode, with sample Magic: The Gathering cards loaded and displayed in a search results view.

### Usage

```bash
# Basic usage with default settings
./scripts/take_screenshot.sh

# Custom port and output location
./scripts/take_screenshot.sh -p 9000 -o /path/to/screenshot.png

# Show all options
./scripts/take_screenshot.sh --help
```

### Options

- `-p, --port PORT`: Server port (default: 8080)
- `-w, --workers WORKERS`: Number of workers (default: 2)
- `-o, --output PATH`: Screenshot output path (default: ./scryfallos-screenshot.png)
- `-t, --timeout SECONDS`: Screenshot timeout (default: 120)
- `-h, --help`: Show help message

### Environment Variables

You can also configure the script using environment variables:

- `PORT`: Server port
- `WORKERS`: Number of workers
- `SCREENSHOT_PATH`: Screenshot output path
- `TIMEOUT_SECONDS`: Screenshot timeout

### Dependencies

The script automatically checks for required dependencies:

- `python3`: Python runtime
- `google-chrome`: Chrome browser for headless screenshots
- `curl`: HTTP client for API testing

### What the Script Does

1. **Dependency Check**: Verifies all required tools are installed
2. **Environment Setup**: Creates Python virtual environment and installs dependencies
3. **Server Start**: Launches the Scryfall OS API server with testcontainers database
4. **Card Import**: Imports sample Magic cards:
   - Lightning Bolt
   - Black Lotus
   - Path to Exile
   - Llanowar Elves
   - Brainstorm
   - Sol Ring
5. **Verification**: Tests search functionality and base page
6. **Screenshot**: Captures the web interface using Chrome headless mode
7. **Cleanup**: Stops server and cleans up resources

### Screenshot Details

The generated screenshot shows:
- Dark mode interface (forced via Chrome flags)
- Search results for "cmc" query
- Cards sorted by USD price in descending order
- Responsive grid layout showing card images and details

### Technical Notes

- Uses testcontainers for automatic PostgreSQL database setup
- Automatically trims white borders using ImageMagick if available
- Uses Chrome's force-dark-mode feature for consistent theming
- Includes proper cleanup to avoid resource leaks
- Supports high-resolution screenshots (2200x2200 window size)

### Troubleshooting

**Script fails with "Server failed to start":**
- Check that ports 8080 (or specified port) is available
- Ensure Docker daemon is running (required for testcontainers)
- Verify Python virtual environment has all dependencies

**Screenshot is blank or small:**
- Ensure Chrome is properly installed
- Check that the server is responding to HTTP requests
- Verify cards were successfully imported

**Permission errors:**
- Make sure the script is executable: `chmod +x scripts/take_screenshot.sh`
- Ensure output directory is writable

### Example Output

```
[2025-09-24 01:42:11] Starting Scryfall OS screenshot generation...
[2025-09-24 01:42:11] Configuration: Port=8080, Workers=2, Output=./scryfallos-screenshot.png
[SUCCESS] All dependencies found
[SUCCESS] Python environment ready
[SUCCESS] Server started successfully (PID: 7726)
[SUCCESS] Card import completed
[SUCCESS] Search endpoint working - received response
[SUCCESS] Base page working - received response
[SUCCESS] Screenshot trimmed and saved to: ./scryfallos-screenshot.png
[SUCCESS] Screenshot appears to have content (111683 bytes)
[SUCCESS] Screenshot generation completed successfully!
```

The script typically completes in 1-2 minutes on first run, or under 30 seconds on subsequent runs when containers are already available.