"""Script to copy card images to S3.

This script:
1. Fetches card data from the database (set_code, collector_number, image_location_uuid)
2. Downloads PNG images from Scryfall
3. Converts them to WebP at 3 different sizes (lg, med, sm)
4. Uploads to S3: s3://biblioplex/setcode/collectornumber/{sm,med,lg}.webp
"""

import argparse
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import boto3
import psycopg
import requests
from botocore.exceptions import ClientError

from api.utils.db_utils import configure_connection, get_pg_creds, get_testcontainers_creds

logger = logging.getLogger(__name__)

# Image size configuration
LARGE_WIDTH = 745  # Full resolution width in pixels
SMALL_WIDTH = 220  # Small resolution width in pixels

# WebP quality setting
WEBP_QUALITY = 85


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
    if not creds:
        creds = get_testcontainers_creds()
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

        conditions = ["image_location_uuid IS NOT NULL"]

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
                image_location_uuid
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


def build_scryfall_image_url(image_location_uuid: str, size: str = "png") -> str:
    """Build Scryfall image URL.

    Args:
        image_location_uuid: UUID of the image location
        size: Image size (png, large, normal, small)

    Returns:
        Full URL to the image
    """
    id_prefix = image_location_uuid[0]
    id_suffix = image_location_uuid[1]
    return f"https://cards.scryfall.io/{size}/front/{id_prefix}/{id_suffix}/{image_location_uuid}.jpg"


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
    skip_existing: bool = True,
) -> bool:
    """Upload a file to S3.

    Args:
        s3_client: Boto3 S3 client
        local_path: Path to local file
        bucket: S3 bucket name
        key: S3 object key
        skip_existing: Skip upload if file already exists

    Returns:
        True if successful or skipped, False otherwise
    """
    try:
        if skip_existing and check_s3_file_exists(s3_client, bucket, key):
            logger.debug(f"Skipping existing S3 file: s3://{bucket}/{key}")
            return True

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
    skip_existing: bool = True,
    dry_run: bool = False,
) -> dict[str, bool]:
    """Process a single card: download, convert, and upload.

    Args:
        card: Card data dict with card_set_code, collector_number, image_location_uuid
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        skip_existing: Skip if files already exist in S3
        dry_run: If True, skip actual downloads and uploads

    Returns:
        Dict with success status for each size (sm, med, lg)
    """
    set_code = card["card_set_code"]
    collector_number = card["collector_number"]
    image_uuid = card["image_location_uuid"]

    if not set_code or not collector_number or not image_uuid:
        logger.warning(f"Skipping card with missing data: {card}")
        return {"sm": False, "med": False, "lg": False}

    logger.info(f"Processing {set_code}/{collector_number}")

    # Check if all files exist in S3
    if skip_existing:
        s3_keys = {
            "sm": f"{set_code}/{collector_number}/sm.webp",
            "med": f"{set_code}/{collector_number}/med.webp",
            "lg": f"{set_code}/{collector_number}/lg.webp",
        }
        all_exist = all(
            check_s3_file_exists(s3_client, bucket, key)
            for key in s3_keys.values()
        )
        if all_exist:
            logger.info(f"All sizes already exist for {set_code}/{collector_number}, skipping")
            return {"sm": True, "med": True, "lg": True}

    if dry_run:
        logger.info(f"[DRY RUN] Would process {set_code}/{collector_number}")
        return {"sm": True, "med": True, "lg": True}

    results = {"sm": False, "med": False, "lg": False}

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Download PNG image from Scryfall
        png_url = build_scryfall_image_url(image_uuid, "png")
        png_path = temp_path / "original.png"

        if not download_image(png_url, png_path):
            return results

        # Calculate sizes
        medium_width = calculate_medium_width(LARGE_WIDTH)

        sizes = {
            "lg": LARGE_WIDTH,
            "med": medium_width,
            "sm": SMALL_WIDTH,
        }

        # Convert and upload each size
        for size_name, width in sizes.items():
            webp_path = temp_path / f"{size_name}.webp"

            if not convert_to_webp(png_path, webp_path, width):
                continue

            s3_key = f"{set_code}/{collector_number}/{size_name}.webp"
            if upload_to_s3(s3_client, webp_path, bucket, s3_key, skip_existing):
                results[size_name] = True

    success_count = sum(results.values())
    logger.info(f"Completed {set_code}/{collector_number}: {success_count}/3 sizes uploaded")

    return results


def main() -> None:
    """Main entry point for the script."""
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

    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.dry_run:
        logger.info("Running in DRY RUN mode - no actual downloads or uploads")

    # Check for cwebp
    try:
        subprocess.run(["cwebp", "-version"], capture_output=True, check=True, timeout=5)  # noqa: S607
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error(
            "cwebp not found. Please install webp tools:\n"
            "  Ubuntu/Debian: sudo apt-get install webp\n"
            "  macOS: brew install webp",
        )
        return

    # Connect to database
    logger.info("Connecting to database...")
    conn = get_database_connection()

    # Fetch cards
    logger.info(f"Fetching cards from database (set={args.set_code}, limit={args.limit})...")
    cards = fetch_cards_from_db(conn, limit=args.limit, set_code=args.set_code)
    conn.close()

    if not cards:
        logger.warning("No cards found to process")
        return

    # Initialize S3 client
    logger.info("Initializing S3 client...")
    s3_client = boto3.client("s3")

    # Process cards
    total_cards = len(cards)
    successful_cards = 0
    failed_cards = 0

    for i, card in enumerate(cards, 1):
        logger.info(f"Processing card {i}/{total_cards}")

        results = process_card(
            card,
            s3_client,
            args.bucket,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )

        if all(results.values()):
            successful_cards += 1
        else:
            failed_cards += 1

    logger.info(
        f"Processing complete: {successful_cards} successful, "
        f"{failed_cards} failed out of {total_cards} total",
    )


if __name__ == "__main__":
    main()
