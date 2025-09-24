#!/bin/bash

# Scryfall OS Screenshot Generation Script
# This script replicates the procedure used to generate the README screenshot
# showing the dark mode interface with search results

set -e

# Configuration
PORT=${PORT:-8080}
WORKERS=${WORKERS:-2}
SCREENSHOT_PATH=${SCREENSHOT_PATH:-./scryfallos-screenshot.png}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-120}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check dependencies
check_dependencies() {
    log "Checking dependencies..."
    
    local missing_deps=()
    
    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi
    
    if ! command -v google-chrome &> /dev/null; then
        missing_deps+=("google-chrome")
    fi
    
    if ! command -v curl &> /dev/null; then
        missing_deps+=("curl")
    fi
    
    if ! command -v convert &> /dev/null; then
        missing_deps+=("imagemagick")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        error "Missing dependencies: ${missing_deps[*]}"
        error "Please install the missing dependencies and try again."
        exit 1
    fi
    
    success "All dependencies found"
}

# Setup Python environment
setup_environment() {
    log "Setting up Python environment..."
    
    if [ ! -d "venv" ]; then
        log "Creating virtual environment..."
        python3 -m venv venv
    fi
    
    source venv/bin/activate
    
    # Install uv if not present
    if ! command -v uv &> /dev/null; then
        log "Installing uv package manager..."
        python -m pip install uv
    fi
    
    # Install dependencies
    log "Installing Python dependencies..."
    uv pip install -r requirements.txt -r test-requirements.txt
    
    # Install webserver dependencies if libev-dev is available
    if pkg-config --exists libev 2>/dev/null; then
        log "Installing webserver dependencies..."
        uv pip install -r webserver-requirements.txt
    else
        warn "libev-dev not found. Webserver dependencies skipped."
        warn "Install with: sudo apt-get install -y libev-dev"
    fi
    
    success "Python environment ready"
}

# Start the API server
start_server() {
    log "Starting API server on port $PORT with $WORKERS workers..."
    
    # Create data directory
    mkdir -p data/api
    
    # Start server in background
    source venv/bin/activate
    python -m api.entrypoint --port $PORT --workers $WORKERS &
    SERVER_PID=$!
    
    # Wait for server to start
    log "Waiting for server to start..."
    local retries=0
    local max_retries=30
    
    while [ $retries -lt $max_retries ]; do
        if curl -s "http://localhost:$PORT/" > /dev/null 2>&1; then
            success "Server started successfully (PID: $SERVER_PID)"
            return 0
        fi
        sleep 2
        ((retries++))
        log "Waiting for server... ($retries/$max_retries)"
    done
    
    error "Server failed to start within $((max_retries * 2)) seconds"
    return 1
}

# Import sample cards
import_cards() {
    log "Importing sample cards..."
    
    local cards=(
        "Lightning Bolt"
        "Black Lotus" 
        "Path to Exile"
        "Llanowar Elves"
        "Brainstorm"
        "Sol Ring"
    )
    
    for card in "${cards[@]}"; do
        log "Importing: $card"
        local encoded_card=$(echo "$card" | sed 's/ /%20/g')
        curl -s "http://localhost:$PORT/import_cards_by_search?search_query=name:%22$encoded_card%22" > /dev/null
        if [ $? -eq 0 ]; then
            log "✓ Imported: $card"
        else
            warn "Failed to import: $card"
        fi
    done
    
    success "Card import completed"
}

# Verify search functionality
verify_search() {
    log "Verifying search functionality..."
    
    local response=$(curl -s "http://localhost:$PORT/search?q=cmc%3C10")
    local card_count=$(echo "$response" | grep -o '"total_cards":[0-9]*' | cut -d':' -f2)
    
    if [ "$card_count" -gt 0 ]; then
        success "Search working - found $card_count cards"
    else
        error "Search not working properly"
        return 1
    fi
}

# Take screenshot using Chrome
take_screenshot() {
    log "Taking screenshot using Chrome..."
    
    local temp_screenshot="/tmp/scryfallos-temp-screenshot.png"
    local search_url="http://localhost:$PORT/?q=cmc%3C10&orderby=edhrec&direction=asc"
    
    # Remove any existing temp screenshot
    rm -f "$temp_screenshot"
    
    log "Capturing page: $search_url"
    
    timeout $TIMEOUT_SECONDS google-chrome \
        --headless=new \
        --disable-gpu \
        --no-sandbox \
        --disable-dev-shm-usage \
        --disable-web-security \
        --allow-running-insecure-content \
        --disable-background-timer-throttling \
        --disable-backgrounding-occluded-windows \
        --disable-renderer-backgrounding \
        --disable-field-trial-config \
        --disable-ipc-flooding-protection \
        --enable-logging \
        --log-level=0 \
        --disable-extensions \
        --disable-plugins \
        --disable-default-apps \
        --allow-external-pages \
        --disable-translate \
        --force-dark-mode \
        --enable-features=WebUIDarkMode \
        --window-size=2200,2022 \
        --virtual-time-budget=20000 \
        --run-all-compositor-stages-before-draw \
        --user-data-dir=/tmp/chrome-profile-screenshot \
        --screenshot="$temp_screenshot" \
        "$search_url" 2>/dev/null
    
    if [ -f "$temp_screenshot" ]; then
        local file_size=$(stat -f%z "$temp_screenshot" 2>/dev/null || stat -c%s "$temp_screenshot" 2>/dev/null)
        log "Screenshot captured (${file_size} bytes)"
        
        # Trim white space from the bottom using ImageMagick
        log "Trimming white space from screenshot..."
        if command -v convert &> /dev/null; then
            # Use ImageMagick to trim white space from bottom
            convert "$temp_screenshot" -trim +repage "$temp_screenshot"
            if [ $? -eq 0 ]; then
                local trimmed_size=$(stat -f%z "$temp_screenshot" 2>/dev/null || stat -c%s "$temp_screenshot" 2>/dev/null)
                log "Screenshot trimmed (${trimmed_size} bytes)"
            else
                warn "Failed to trim screenshot, using original"
            fi
        else
            warn "ImageMagick not available, skipping trim"
        fi
        
        # Move to final location
        mv "$temp_screenshot" "$SCREENSHOT_PATH"
        success "Screenshot saved to: $SCREENSHOT_PATH"
        
        # Verify screenshot is not just a white page
        if [ "$file_size" -gt 500000 ]; then
            success "Screenshot appears to have content (${file_size} bytes)"
        else
            warn "Screenshot file size seems small (${file_size} bytes) - may be blank"
        fi
    else
        error "Screenshot file not created"
        return 1
    fi
}

# Cleanup function
cleanup() {
    log "Cleaning up..."
    
    if [ ! -z "$SERVER_PID" ]; then
        log "Stopping server (PID: $SERVER_PID)"
        kill $SERVER_PID 2>/dev/null || true
        wait $SERVER_PID 2>/dev/null || true
    fi
    
    # Clean up Chrome profile
    rm -rf /tmp/chrome-profile-screenshot
    
    log "Cleanup completed"
}

# Print usage information
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Options:
    -p, --port PORT         Server port (default: 8080)
    -w, --workers WORKERS   Number of workers (default: 2)
    -o, --output PATH       Screenshot output path (default: ./scryfallos-screenshot.png)
    -t, --timeout SECONDS   Screenshot timeout (default: 120)
    -h, --help             Show this help message

Environment Variables:
    PORT                   Server port
    WORKERS                Number of workers
    SCREENSHOT_PATH        Screenshot output path
    TIMEOUT_SECONDS        Screenshot timeout

Examples:
    $0                                          # Use defaults
    $0 -p 9000 -w 4                           # Custom port and workers
    $0 -o /tmp/my-screenshot.png               # Custom output path
    PORT=9000 WORKERS=4 $0                     # Using environment variables

This script will:
1. Check dependencies (python3, google-chrome, curl)
2. Set up Python virtual environment and install packages
3. Start the Scryfall OS API server
4. Import sample Magic cards for demonstration
5. Take a screenshot of the dark mode interface
6. Clean up resources

The screenshot shows a search for cards with CMC < 10, ordered by EDHREC rank,
in dark mode with proper card images loaded.
EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -w|--workers)
            WORKERS="$2"
            shift 2
            ;;
        -o|--output)
            SCREENSHOT_PATH="$2"
            shift 2
            ;;
        -t|--timeout)
            TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    log "Starting Scryfall OS screenshot generation..."
    log "Configuration: Port=$PORT, Workers=$WORKERS, Output=$SCREENSHOT_PATH"
    
    # Set up cleanup trap
    trap cleanup EXIT
    
    # Execute steps
    check_dependencies
    setup_environment
    start_server
    import_cards
    verify_search
    take_screenshot
    
    success "Screenshot generation completed successfully!"
    success "Screenshot saved to: $SCREENSHOT_PATH"
    
    if [ -f "$SCREENSHOT_PATH" ]; then
        local file_size=$(stat -f%z "$SCREENSHOT_PATH" 2>/dev/null || stat -c%s "$SCREENSHOT_PATH" 2>/dev/null)
        log "Final screenshot size: ${file_size} bytes"
    fi
}

# Run main function
main "$@"