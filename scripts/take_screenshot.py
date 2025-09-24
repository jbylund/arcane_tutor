#!/usr/bin/env python3
"""Script to start server, load sample cards, and take a screenshot.

This script:
1. Starts the API server in the background
2. Loads sample cards into the database
3. Gets the public IP address of the server
4. Makes a request to screenshotmachine to get a screenshot of the rendered page
"""

import logging
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# Add the project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))


# Constants
SCREENSHOT_API_KEY = "c5816c"
DEFAULT_PORT = 8080
DEFAULT_HOST = "localhost"
STARTUP_TIMEOUT = 30  # seconds to wait for server startup
SCREENSHOT_TIMEOUT = 60  # seconds to wait for screenshot
HTTP_OK = 200


def setup_logging() -> None:
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def start_server_process(port: int = DEFAULT_PORT) -> subprocess.Popen:
    """Start the API server in a subprocess.

    Args:
        port: Port to run the server on

    Returns:
        The subprocess.Popen object for the server process
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting API server on port %d...", port)

    # Start the server using the entrypoint module
    cmd = [
        sys.executable, "-m", "api.entrypoint",
        "--port", str(port),
        "--workers", "2",  # Use fewer workers for the script
    ]

    # Change to project directory
    server_process = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    logger.info("Server process started with PID %d", server_process.pid)
    return server_process


def wait_for_server(port: int = DEFAULT_PORT, timeout: int = STARTUP_TIMEOUT) -> bool:
    """Wait for the server to be ready to accept requests.

    Args:
        port: Port the server is running on
        timeout: Maximum time to wait in seconds

    Returns:
        True if server is ready, False if timeout
    """
    logger = logging.getLogger(__name__)
    logger.info("Waiting for server to be ready...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://{DEFAULT_HOST}:{port}/db_ready", timeout=5)
            if response.status_code == HTTP_OK and response.json() is True:
                logger.info("Server is ready!")
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pass

        time.sleep(1)

    logger.error("Server failed to start within %d seconds", timeout)
    return False


def load_sample_cards(port: int = DEFAULT_PORT) -> bool:
    """Load sample cards using specific otag searches.

    Args:
        port: Port the server is running on

    Returns:
        True if successful, False otherwise
    """
    logger = logging.getLogger(__name__)
    logger.info("Loading sample cards using otag searches...")

    # Tags for specific cycles as requested
    otags = [
        "cycle-akh-god",
        "cycle-thb-monocolor-god",
        "cycle-ths-major-god",
        "cycle-afr-chromatic-dragon",
        "cycle-chk-dragon",
    ]

    try:
        # First ensure schema is setup
        response = requests.post(f"http://{DEFAULT_HOST}:{port}/setup_schema", timeout=30)
        if response.status_code != HTTP_OK:
            logger.error("Failed to setup schema: %s", response.text)
            return False

        # Import cards for each otag
        total_cards_loaded = 0
        for otag in otags:
            logger.info("Importing cards for otag: %s", otag)
            response = requests.post(
                f"http://{DEFAULT_HOST}:{port}/import_cards_by_search",
                params={"search_query": f"otag:{otag}"},
                timeout=60,
            )
            if response.status_code != HTTP_OK:
                logger.error("Failed to import cards for otag %s: %s", otag, response.text)
                return False

            result = response.json()
            cards_loaded = result.get("cards_loaded", 0)
            total_cards_loaded += cards_loaded
            logger.info("Loaded %d cards for otag: %s", cards_loaded, otag)

        logger.info("Sample cards loaded successfully: %d total cards", total_cards_loaded)
        return True

    except requests.exceptions.RequestException as e:
        logger.error("Error loading sample cards: %s", e)
        return False


def get_public_ip() -> str | None:
    """Get the public IP address using ipify.org.

    Returns:
        The public IP address, or None if failed
    """
    logger = logging.getLogger(__name__)
    logger.info("Getting public IP address from ipify.org...")

    try:
        response = requests.get("https://api.ipify.org/?format=json", timeout=10)
        response.raise_for_status()

        result = response.json()
        public_ip = result.get("ip")

        if not public_ip:
            logger.error("No IP address found in response: %s", result)
            return None

        logger.info("Public IP address: %s", public_ip)
        return public_ip

    except requests.exceptions.RequestException as e:
        logger.error("Error getting public IP: %s", e)
        return None


def take_screenshot(
    public_ip: str,
    port: int = DEFAULT_PORT,
    query: str = "t:beast",
    orderby: str = "edhrec",
    direction: str = "asc",
) -> dict | None:
    """Take a screenshot of the rendered page.

    Args:
        public_ip: The public IP address of the server
        port: Port the server is running on
        query: Search query for the screenshot
        orderby: Sort order
        direction: Sort direction

    Returns:
        Screenshot result dictionary, or None if failed
    """
    logger = logging.getLogger(__name__)
    logger.info("Taking screenshot...")

    try:
        # Construct the target URL
        encoded_query = urllib.parse.quote(f"q={urllib.parse.quote(query)}&orderby={orderby}&direction={direction}")
        target_url = f"https://{public_ip}:{port}/?{encoded_query}"

        # Prepare screenshotmachine API parameters
        screenshot_params = {
            "key": SCREENSHOT_API_KEY,
            "url": target_url,
            "device": "desktop",
            "dimension": "1900x1900",
            "format": "png",
            "cacheLimit": "0",
            "delay": "2000",
            "user-agent": "screenshotter",
        }

        # Make request to screenshotmachine API
        screenshot_url = "https://api.screenshotmachine.com/"
        logger.info("Requesting screenshot from %s", screenshot_url)
        logger.info("Target URL: %s", target_url)

        response = requests.get(screenshot_url, params=screenshot_params, timeout=SCREENSHOT_TIMEOUT)
        response.raise_for_status()

        # Check if response is an image or error message
        content_type = response.headers.get("content-type", "")
        screenshot_size = len(response.content)

        result = {
            "target_url": target_url,
            "screenshot_url": f"{screenshot_url}?{urllib.parse.urlencode(screenshot_params)}",
            "screenshot_size_bytes": screenshot_size,
            "content_type": content_type,
        }

        if "image" in content_type:
            logger.info("Screenshot taken successfully: %d bytes, content-type: %s", screenshot_size, content_type)
            result["status"] = "success"
            result["message"] = "Screenshot taken successfully"

            # Save screenshot to file
            screenshot_filename = f"screenshot_{int(time.time())}.png"
            screenshot_path = Path(screenshot_filename)
            with screenshot_path.open("wb") as f:
                f.write(response.content)
            result["filename"] = screenshot_filename
            logger.info("Screenshot saved to: %s", screenshot_filename)

        else:
            error_message = response.text
            logger.error("Screenshot API returned error: %s", error_message)
            result["status"] = "error"
            result["message"] = f"Screenshot service error: {error_message}"

        return result

    except requests.exceptions.RequestException as e:
        logger.error("Error taking screenshot: %s", e)
        return None


def cleanup_server(server_process: subprocess.Popen) -> None:
    """Clean up the server process.

    Args:
        server_process: The server process to terminate
    """
    logger = logging.getLogger(__name__)
    logger.info("Cleaning up server process...")

    try:
        # Try graceful termination first
        server_process.terminate()

        # Wait up to 5 seconds for graceful termination
        try:
            server_process.wait(timeout=5)
            logger.info("Server terminated gracefully")
        except subprocess.TimeoutExpired:
            # Force kill if graceful termination fails
            logger.warning("Server didn't terminate gracefully, forcing kill...")
            server_process.kill()
            server_process.wait()
            logger.info("Server killed")

    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Error cleaning up server: %s", e)


def main() -> None:
    """Main function to orchestrate the screenshot process."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting screenshot script...")

    server_process = None
    try:
        # Step 1: Start the server
        server_process = start_server_process()

        # Step 2: Wait for server to be ready
        if not wait_for_server():
            logger.error("Server failed to start, exiting")
            return

        # Step 3: Load sample cards
        if not load_sample_cards():
            logger.error("Failed to load sample cards, exiting")
            return

        # Step 4: Get public IP
        public_ip = get_public_ip()
        if not public_ip:
            logger.error("Failed to get public IP, exiting")
            return

        # Step 5: Take screenshot
        result = take_screenshot(public_ip)
        if not result:
            logger.error("Failed to take screenshot")
            return

        # Print results
        logger.info("Screenshot process completed successfully!")
        logger.info("Results:")
        for key, value in result.items():
            logger.info("  %s: %s", key, value)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
    finally:
        # Always clean up the server process
        if server_process:
            cleanup_server(server_process)


if __name__ == "__main__":
    main()
