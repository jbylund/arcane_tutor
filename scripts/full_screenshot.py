"""Script for taking full screenshots of the scryfallos application."""
from __future__ import annotations

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
    logging.basicConfig(level=logging.INFO)
    base_url = "http://127.0.0.1:8080"
    with ServerContext():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Set viewport to custom width x height
            context = browser.new_context(
                viewport={"width": 2200, "height": 3000},
            )
            page = context.new_page()
            url = f"{base_url}/?q=name%3Abeast+or+t%3Abeast&orderby=edhrec&direction=asc"
            page.goto(
                url,
                wait_until="networkidle",
                timeout=30*MILLISECONDS,
            )
            page.wait_for_timeout(20)  # give it a moment for JS to settle

            # Screenshot of just the viewport
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            page.screenshot(
                full_page=False,
                path=f"scryfallos-{timestamp}.png",
                # quality=100, - no quality for png
                timeout=30*MILLISECONDS,
                type="png",
            )

            browser.close()


if __name__ == "__main__":
    main()
