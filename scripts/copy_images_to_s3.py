"""Script to copy card images to S3.

This script:
1. Fetches card data from the database (set_code, collector_number, image_location_uuid)
2. Downloads PNG images from Scryfall
3. Converts them to WebP at 3 different sizes (lg, med, sm)
4. Uploads to S3: s3://biblioplex/setcode/collectornumber/{sm,med,lg}.webp
"""

import argparse
import datetime
import json
import logging
import math
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import psycopg
import requests
from botocore.exceptions import ClientError

from api.utils.db_utils import configure_connection, get_pg_creds

logger = logging.getLogger(__name__)

# Image size configuration
ORIGINAL_WIDTH = 745  # this seems to be the size of the pngs that scryfall returns
LARGE_WIDTH = 745  # Full resolution width in pixels
MEDIUM_WIDTH = 410  # Medium resolution width in pixels
SMALL_WIDTH = 220  # Small resolution width in pixels

# WebP quality setting
WEBP_QUALITY = 85

LARGE_KEY = "745"
MEDIUM_KEY = "410"
SMALL_KEY = "220"

ORIGINAL_KEY = "o"

def setup_logging(verbose: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def get_database_connection() -> psycopg.Connection:
    """Get a connection to the PostgreSQL database."""
    creds = get_pg_creds()
    conninfo = " ".join(f"{k}={v}" for k, v in creds.items())
    conn = psycopg.connect(conninfo)
    configure_connection(conn)
    return conn


def fetch_cards_from_db(
    conn: psycopg.Connection,
    limit: int | None = None,
    set_code: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch card data from the database.

    Args:
        conn: Database connection
        limit: Maximum number of cards to fetch (None for all)
        set_code: Filter by specific set code (None for all sets)

    Returns:
        List of dictionaries containing card_set_code, collector_number, and image_location_uuid
    """
    with conn.cursor() as cursor:
        where_clause = ""
        params = []

        conditions = []

        if set_code:
            conditions.append("card_set_code = %s")
            params.append(set_code)

        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        limit_clause = f"LIMIT {limit}" if limit else ""

        query = f"""
            SELECT
                card_set_code,
                collector_number,
                raw_card_blob->'image_uris'->>'png' as png_url
            FROM magic.cards
            {where_clause}
            ORDER BY card_set_code, collector_number_int NULLS LAST, collector_number
            {limit_clause}
        """

        cursor.execute(query, params)
        cards = cursor.fetchall()

    logger.info(f"Fetched {len(cards)} cards from database")
    return cards


def calculate_medium_width(full_width: int) -> int:
    """Calculate medium image width using sqrt(220 * full_width)."""
    return int(math.sqrt(SMALL_WIDTH * full_width))


def download_image(url: str, output_path: Path) -> bool:
    """Download an image from a URL.

    Args:
        url: URL to download from
        output_path: Path to save the image

    Returns:
        True if successful, False otherwise
    """
    try:
        response = requests.get(url, timeout=30, stream=True)
        response.raise_for_status()

        with output_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.debug(f"Downloaded image to {output_path}")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to download image from {url}: {e}")
        return False


def convert_to_webp(
    input_path: Path,
    output_path: Path,
    width: int,
    quality: int = WEBP_QUALITY,
) -> bool:
    """Convert an image to WebP format with resizing.

    Args:
        input_path: Path to input image (PNG or JPG)
        output_path: Path to save WebP output
        width: Target width in pixels (height auto-calculated)
        quality: WebP quality (0-100)

    Returns:
        True if successful, False otherwise
    """
    try:
        cmd = [
            "cwebp",
            str(input_path),
            "-resize", str(width), "0",
            "-q", str(quality),
            "-sharp_yuv",
            "-o", str(output_path),
        ]

        subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

        logger.debug(f"Converted image to WebP: {output_path} (width={width}px)")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to convert image to WebP: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error("cwebp command not found. Please install webp tools: apt-get install webp")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout converting image {input_path}")
        return False


def check_s3_file_exists(s3_client: Any, bucket: str, key: str) -> bool:  # noqa: ANN401
    """Check if a file exists in S3.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        True if file exists, False otherwise
    """
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        logger.error(f"Error checking S3 file {bucket}/{key}: {e}")
        return False


def upload_to_s3(
    s3_client: Any,  # noqa: ANN401
    local_path: Path,
    bucket: str,
    key: str,
) -> bool:
    """Upload a file to S3.

    Args:
        s3_client: Boto3 S3 client
        local_path: Path to local file
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        True if successful or skipped, False otherwise
    """
    try:
        s3_client.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={"ContentType": "image/webp"},
        )
        logger.debug(f"Uploaded to S3: s3://{bucket}/{key}")
        return True
    except ClientError as e:
        logger.error(f"Failed to upload to S3 {bucket}/{key}: {e}")
        return False


def process_card(
    card: dict[str, Any],
    s3_client: Any,  # noqa: ANN401
    bucket: str,
    dry_run: bool = False,
) -> dict[str, bool]:
    """Process a single card: download, convert, and upload.

    Args:
        card: Card data dict with card_set_code, collector_number, image_location_uuid
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        dry_run: If True, skip actual downloads and uploads

    Returns:
        Dict with success status for each size (sm, med, lg)
    """
    set_code = card["card_set_code"]
    collector_number = card["collector_number"]
    png_url = card["png_url"]

    if not set_code or not collector_number or not png_url:
        logger.warning(f"Skipping card with missing data: {card}")
        return {SMALL_KEY: False, MEDIUM_KEY: False, LARGE_KEY: False}

    logger.info("Processing %s/%s", set_code, collector_number)

    if dry_run:
        logger.info(f"[DRY RUN] Would process {set_code}/{collector_number}")
        return {SMALL_KEY: True, MEDIUM_KEY: True, LARGE_KEY: True}

    results = {SMALL_KEY: False, MEDIUM_KEY: False, LARGE_KEY: False}

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Download PNG image from Scryfall
        png_path = temp_path / "original.png"

        if not download_image(png_url, png_path):
            return results

        sizes = {
            LARGE_KEY: LARGE_WIDTH,
            MEDIUM_KEY: MEDIUM_WIDTH,
            SMALL_KEY: SMALL_WIDTH,
        }

        # Convert and upload each size
        for size_name, width in sizes.items():
            webp_path = temp_path / f"{size_name}.webp"

            if not convert_to_webp(png_path, webp_path, width):
                continue

            s3_key = f"img/{set_code}/{collector_number}/{size_name}.webp"
            if upload_to_s3(s3_client, webp_path, bucket, s3_key):
                results[size_name] = True

    success_count = sum(results.values())
    logger.info(f"Completed {set_code}/{collector_number}: {success_count}/3 sizes uploaded")

    return results


class CardProcessorPool:
    """Multiprocessing worker pool for processing cards.

    Each worker process gets its own S3 client initialized once via init_worker.
    This avoids creating a new boto3 client for every card while keeping
    the S3 client namespaced within a class instead of as a global variable.
    """

    s3_client = None

    @classmethod
    def init_worker(cls) -> None:
        """Initialize worker process with S3 client.

        This runs once per worker process when the pool is created.
        Sets cls.s3_client which is separate per worker process.
        """
        cls.s3_client = boto3.client("s3")

    @classmethod
    def process_card_worker(cls, job_task: dict[str, Any]) -> dict[str, bool]:
        """Worker function for parallel processing of cards.

        Uses the class-level S3 client initialized once per worker process.

        Args:
            job_task: Dict of job task

        Returns:
            Dict with success status for each size (sm, med, lg)
        """
        bucket = job_task.pop("bucket")
        dry_run = job_task.pop("dry_run")
        return process_card(job_task, cls.s3_client, bucket, dry_run)


@dataclass
class Args:
    """Command-line arguments for the image copy script.

    Attributes:
        bucket: S3 bucket name to upload images to
        set_code: Optional set code to filter cards by
        limit: Optional limit on number of cards to process
        skip_existing: Whether to skip cards that already have images in S3
        dry_run: If True, simulate the process without actual downloads/uploads
        verbose: Enable verbose logging output
        workers: Number of parallel worker processes for image processing
    """
    bucket: str = "biblioplex"
    set_code: str | None = None
    limit: int | None = None
    skip_existing: bool = True
    dry_run: bool = False
    verbose: bool = False
    workers: int = 8

def get_args() -> Args:
    """Parse command-line arguments and return Args dataclass.

    Returns:
        Args object containing parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        description="Copy card images to S3 with WebP conversion",
    )
    parser.add_argument(
        "--bucket",
        default="biblioplex",
        help="S3 bucket name (default: biblioplex)",
    )
    parser.add_argument(
        "--set",
        dest="set_code",
        help="Process only cards from a specific set (e.g., 'iko')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of cards to process",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip cards that already have all images in S3 (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_false",
        dest="skip_existing",
        help="Re-process cards even if images exist in S3",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode - don't actually download or upload",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel worker processes (default: 8)",
    )
    return Args(**vars(parser.parse_args()))


def configure_env() -> None:
    """Load environment variables from env.json file."""
    with Path("env.json").open("r") as f:
        env = json.load(f)
    os.environ.update(env)

def check_cwebp() -> None:
    """Check if cwebp command is available and exit if not found."""
    try:
        subprocess.run(["cwebp", "-version"], capture_output=True, check=True, timeout=5)  # noqa: S607
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error(
            "cwebp not found. Please install webp tools:\n"
            "  Ubuntu/Debian: sudo apt-get install webp\n"
            "  macOS: brew install webp",
        )
        sys.exit(1)

def get_db_cards(args: Args) -> list[dict[str, Any]]:
    """Get all cards in the database."""
    logger.info("Connecting to database...")
    conn = get_database_connection()

    # Fetch cards
    logger.info("Fetching cards from database (set=%s, limit=%s)...", args.set_code, args.limit)
    db_cards = fetch_cards_from_db(conn, limit=args.limit, set_code=args.set_code)
    conn.close()

    if not db_cards:
        logger.warning("No cards found to process")
        return None

    logger.info("Found %d cards in database, should create %d images", len(db_cards), len(db_cards) * 3)
    return db_cards


def get_s3_cards(args: Args) -> set[tuple[str, str, str]]:
    """Get all cards in S3."""
    s3resource = boto3.resource("s3")
    bucket = s3resource.Bucket(args.bucket)
    s3_cards = set()
    for obj in bucket.objects.filter(Prefix="img/", MaxKeys=9999999):
        if not obj.key.endswith(".webp"):
            continue
        try:
            # discard the img/ prefix
            _, _, obj_key = obj.key.partition("/")
            set_code, collector_number, size_webp = obj_key.split("/")
            size = size_webp.partition(".")[0]
            s3_cards.add((set_code, collector_number, size))
        except ValueError:
            continue

    distinct_s3_cards = {(set_code, collector_number) for (set_code, collector_number, _size) in s3_cards}
    logger.info("Found %d image objects in S3, belonging to %d distinct cards", len(s3_cards), len(distinct_s3_cards))
    return s3_cards

def main() -> None:
    """Main entry point for the script."""
    args = get_args()
    setup_logging(args.verbose)

    if args.dry_run:
        logger.info("Running in DRY RUN mode - no actual downloads or uploads")

    check_cwebp()
    configure_env()

    db_cards = get_db_cards(args)
    s3_cards = get_s3_cards(args)

    cards_with_missing_images = []
    for icard in db_cards:
        set_code = icard["card_set_code"]
        collector_number = icard["collector_number"]
        missing_for_card = []
        for size in [SMALL_KEY, MEDIUM_KEY, LARGE_KEY]:
            key = (set_code, collector_number, size)
            if key not in s3_cards:
                missing_for_card.append(size)
        if missing_for_card:
            cards_with_missing_images.append(
                {
                    "card_set_code": set_code,
                    "collector_number": collector_number,
                    "png_url": icard["png_url"],
                    "sizes": missing_for_card,
                    "bucket": args.bucket,
                    "dry_run": args.dry_run,
                },
            )
    logger.info("Found %d cards with missing images", len(cards_with_missing_images))

    # Process cards in parallel
    logger.info("Processing cards using %d worker processes", args.workers)

    successful_cards = failed_cards = 0
    start_time = time.monotonic()

    pool = multiprocessing.Pool(processes=args.workers, initializer=CardProcessorPool.init_worker)
    try:
        # Use imap_unordered for better progress tracking
        for idx, results in enumerate(
            pool.imap_unordered(
                func=CardProcessorPool.process_card_worker,
                iterable=cards_with_missing_images,
            ),
            1,
        ):
            if (idx and idx % 10 == 0) or idx == len(cards_with_missing_images):
                elapsed_time = time.monotonic() - start_time
                fraction_complete = idx / len(cards_with_missing_images)
                estimated_time_remaining = (elapsed_time / fraction_complete) - elapsed_time
                estimated_remaining_duration = datetime.timedelta(seconds=round(estimated_time_remaining, 1))
                logger.info("Progress: %d / %d cards processed (ETA: %s)", idx, len(cards_with_missing_images), estimated_remaining_duration)

            if all(results.values()):
                successful_cards += 1
            else:
                failed_cards += 1
    finally:
        # Properly clean up the pool to avoid weakref finalize errors
        pool.close()  # Prevent new tasks from being submitted
        pool.join()   # Wait for all worker processes to finish

    logger.info(
        "Processing complete: %d successful, %d failed out of %d total",
        successful_cards,
        failed_cards,
        len(cards_with_missing_images),
    )


if __name__ == "__main__":
    main()
