#!/usr/bin/env python3
"""Entrypoint for the Scryfall cache service."""

import argparse
import logging
from wsgiref import simple_server

from cache_service import create_app


def main() -> None:
    """Main entrypoint for the cache service."""
    parser = argparse.ArgumentParser(description="Scryfall Cache Service")
    parser.add_argument("--port", type=int, default=8081, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--workers", type=int, default=2, help="Number of worker processes")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.info("Starting Scryfall Cache Service on %s:%d", args.host, args.port)

    # Create the WSGI application
    app = create_app()

    # For development, use simple WSGI server
    if args.debug:
        with simple_server.make_server(args.host, args.port, app) as httpd:
            logger.info("Serving on http://%s:%d", args.host, args.port)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                logger.info("Shutting down...")
    else:
        # For production, recommend using gunicorn
        logger.info("Use gunicorn for production:")
        logger.info("gunicorn --bind %s:%d --workers %d cache_service:create_app()",
                   args.host, args.port, args.workers)


if __name__ == "__main__":
    main()
