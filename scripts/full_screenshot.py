"""Script for taking full screenshots of the application."""
from __future__ import annotations

import argparse
import logging
import time
from multiprocessing import Process
from typing import TYPE_CHECKING

import requests
from playwright.sync_api import sync_playwright

from api.entrypoint import run_server

if TYPE_CHECKING:
    from types import TracebackType

MILLISECONDS = 1000
logger = logging.getLogger(__name__)

class ServerContext:
    """Context manager which manages a scryfall context."""
    def __init__(self) -> None:
        """Initialize the server context with default port and worker count."""
        self.port = 8080
        self.num_workers = 4
        self.server_process = Process(target=run_server, kwargs={"port": self.port, "num_workers": self.num_workers})
        self.server_process.start()

    def __enter__(self) -> ServerContext:
        """Enter the server context."""
        if not self.server_process.is_alive():
            msg = "Server process is not alive"
            raise AssertionError(msg)
        deadline = time.monotonic() + 5
        while True:
            # try to connect to the server
            try:
                requests.get(f"http://127.0.0.1:{self.port}/notfound", timeout=5)
                break
            except requests.exceptions.ConnectionError:
                if deadline < time.monotonic():
                    raise
                time.sleep(0.1)
        logger.info("Server is ready")
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Exit the server context."""
        self.server_process.terminate()
        self.server_process.join(timeout=3)

        if self.server_process.is_alive():
            logger.warning("Server process is still alive, killing it")
            self.server_process.kill()
            self.server_process.join(timeout=1)

        logger.info("Server cleanup completed")

def main() -> None:
    """Main entrypoint for the full screenshot script."""
    parser = argparse.ArgumentParser(description="Take screenshots of the application")
    parser.add_argument("--width", type=int, default=2200, help="Viewport width in pixels (default: 2200)")
    parser.add_argument("--height", type=int, default=3000, help="Viewport height in pixels (default: 3000)")
    parser.add_argument("--output", type=str, help="Output filename (default: {timestamp}.png)")
    parser.add_argument("--full-page", action="store_true", help="Take full page screenshot instead of viewport only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    base_url = "http://127.0.0.1:8080"

    # Generate default filename if not provided
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_filename = args.output or f"{args.width}x{args.height}-{timestamp}.png"

    with ServerContext():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Set viewport to custom width x height
            context = browser.new_context(
                viewport={"width": args.width, "height": args.height},
            )
            page = context.new_page()
            url = f"{base_url}/?q=name%3Abeast+or+t%3Abeast&orderby=edhrec&direction=asc"
            page.goto(
                url,
                wait_until="networkidle",
                timeout=30*MILLISECONDS,
            )
            page.wait_for_timeout(20)  # give it a moment for JS to settle

            # Screenshot of viewport or full page
            page.screenshot(
                full_page=args.full_page,
                path=output_filename,
                # quality=100, - no quality for png
                timeout=30*MILLISECONDS,
                type="png",
            )

            logger.info(f"Screenshot saved as: {output_filename}")
            browser.close()


if __name__ == "__main__":
    main()
