#!/usr/bin/env python3
"""Convert and subset the MPlantin font to WOFF/WOFF2 for web delivery.

This script:
1. Takes the MPlantin OTF font from fonts/mplantin.otf
2. Subsets it to include only Latin characters (reducing file size significantly)
3. Generates optimized CSS with font-display: swap
4. Optionally uploads to S3/CloudFront with proper headers

Requirements:
    pip install fonttools brotli boto3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


# Unicode ranges for subsetting (Latin characters + common punctuation)
# This covers: Basic Latin, Latin-1 Supplement, and Latin Extended-A
# Enough for English card text and common European characters
UNICODE_RANGES = [
    "U+0020-007F",  # Basic Latin (space through tilde)
    "U+00A0-00FF",  # Latin-1 Supplement
    "U+0100-017F",  # Latin Extended-A
    "U+2018-201F",  # Smart quotes
    "U+2013-2014",  # En dash, em dash
    "U+2026",  # Ellipsis
]


def check_dependencies() -> None:
    """Check if required tools are installed."""
    try:
        subprocess.run(
            ["pyftsubset", "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: pyftsubset not found. Please install fonttools:")
        print("  pip install fonttools brotli")
        sys.exit(1)


def subset_font(input_font: Path, output_font: Path, font_format: str = "woff2") -> None:
    """Subset the font to include only Latin characters and common punctuation."""
    print(f"Subsetting {input_font.name} to {output_font.name}...")

    unicode_range_arg = ",".join(UNICODE_RANGES)

    cmd = [
        "pyftsubset",
        str(input_font),
        f"--output-file={output_font}",
        f"--flavor={font_format}",
        f"--unicodes={unicode_range_arg}",
        "--layout-features=*",
        "--glyph-names",
        "--symbol-cmap",
        "--legacy-cmap",
        "--notdef-glyph",
        "--notdef-outline",
        "--recommended-glyphs",
        "--name-IDs=*",
        "--name-legacy",
        "--name-languages=*",
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        # Check file sizes
        input_size = input_font.stat().st_size
        output_size = output_font.stat().st_size
        reduction = (1 - output_size / input_size) * 100

        print(f"  → Original: {input_size:,} bytes")
        print(f"  → Subset: {output_size:,} bytes")
        print(f"  → Reduction: {reduction:.1f}%")
    except subprocess.CalledProcessError as e:
        print(f"Error subsetting font: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        raise


def generate_css(output_dir: Path, cdn_base_url: str = "/cdn/fonts/mplantin") -> None:
    """Generate the CSS file with @font-face declarations."""
    css_content = f"""/**
 * MPlantin Font - Optimized subset for card oracle text
 * Contains Latin characters and common punctuation
 *
 * Original font: MPlantin (Plantin MT)
 * Used on Magic: The Gathering cards for oracle text
 */

@font-face {{
  font-family: 'MPlantin';
  src: url('{cdn_base_url}/mplantin-subset.woff2') format('woff2'),
       url('{cdn_base_url}/mplantin-subset.woff') format('woff');
  font-weight: normal;
  font-style: normal;
  font-display: swap;
}}
"""

    css_file = output_dir / "mplantin-subset.css"
    css_file.parent.mkdir(parents=True, exist_ok=True)
    with open(css_file, "w") as f:
        f.write(css_content)

    print(f"Generated CSS: {css_file}")


def configure_bucket_cors(bucket: str) -> bool:
    """Configure CORS on the S3 bucket to allow font loading."""
    if not HAS_BOTO3:
        print("Warning: boto3 not installed, skipping CORS configuration")
        return False

    try:
        s3_client = boto3.client("s3")

        cors_config = {
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "HEAD"],
                    "AllowedOrigins": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                },
            ],
        }

        s3_client.put_bucket_cors(Bucket=bucket, CORSConfiguration=cors_config)
        print(f"✓ Configured CORS on bucket: {bucket}")
        return True
    except (ClientError, NoCredentialsError) as e:
        print(f"Warning: Could not configure CORS: {e}")
        return False


def upload_to_s3(
    file_path: Path,
    bucket: str,
    s3_key: str,
    content_type: str,
    cache_control: str = "public, max-age=31536000, immutable",
) -> bool:
    """Upload a file to S3 with proper headers."""
    if not HAS_BOTO3:
        print("Warning: boto3 not installed, skipping upload")
        return False

    try:
        s3_client = boto3.client("s3")

        extra_args = {
            "ContentType": content_type,
            "CacheControl": cache_control,
        }

        s3_client.upload_file(
            str(file_path),
            bucket,
            s3_key,
            ExtraArgs=extra_args,
        )

        print(f"✓ Uploaded: s3://{bucket}/{s3_key}")
        return True
    except (ClientError, NoCredentialsError) as e:
        print(f"Error uploading to S3: {e}")
        return False


def upload_fonts_to_cloudfront(
    output_dir: Path,
    bucket: str,
    s3_prefix: str = "cdn/fonts/mplantin",
) -> None:
    """Upload all font files to S3/CloudFront with proper headers."""
    if not HAS_BOTO3:
        print("Skipping upload: boto3 not installed")
        return

    # Configure CORS on the bucket first
    configure_bucket_cors(bucket)

    # Ensure s3_prefix doesn't start with / but does end without /
    s3_prefix = s3_prefix.strip("/")

    # Files to upload with their content types
    files_to_upload = [
        ("mplantin-subset.woff2", "font/woff2"),
        ("mplantin-subset.woff", "font/woff"),
        ("mplantin-subset.css", "text/css; charset=utf-8"),
    ]

    for filename, content_type in files_to_upload:
        file_path = output_dir / filename
        if not file_path.exists():
            print(f"Warning: {filename} not found, skipping")
            continue

        s3_key = f"{s3_prefix}/{filename}"
        upload_to_s3(file_path, bucket, s3_key, content_type)


def load_env() -> None:
    """Load environment variables from env.json."""
    env_file = Path(__file__).parent.parent / "env.json"
    if env_file.exists():
        try:
            with open(env_file) as f:
                env_data = json.load(f)
                for key, value in env_data.items():
                    os.environ.setdefault(key, str(value))
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Could not load env.json: {e}")


def main() -> None:
    """Main entry point for the font subsetting tool."""
    parser = argparse.ArgumentParser(
        description="Subset MPlantin font for optimized web delivery",
    )
    parser.add_argument(
        "--input-font",
        type=Path,
        default=Path("fonts/mplantin.otf"),
        help="Input font file (default: fonts/mplantin.otf)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/fonts/mplantin"),
        help="Output directory for subsetted fonts (default: data/fonts/mplantin)",
    )
    parser.add_argument(
        "--cdn-url",
        type=str,
        default="https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mplantin",
        help="CDN base URL for font files",
    )
    parser.add_argument(
        "--s3-bucket",
        type=str,
        help="S3 bucket name for upload (if not specified, fonts are generated locally only)",
    )
    parser.add_argument(
        "--s3-prefix",
        type=str,
        default="cdn/fonts/mplantin",
        help="S3 key prefix (default: cdn/fonts/mplantin)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip uploading to S3 even if bucket is specified",
    )

    args = parser.parse_args()

    # Load environment
    load_env()

    # Check dependencies
    check_dependencies()

    # Check if input font exists
    if not args.input_font.exists():
        print(f"Error: Input font not found: {args.input_font}")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("MPlantin Font Subsetting Tool")
    print("=" * 60)

    # Subset to woff2
    woff2_subset = args.output_dir / "mplantin-subset.woff2"
    subset_font(args.input_font, woff2_subset, "woff2")

    # Subset to woff
    woff_subset = args.output_dir / "mplantin-subset.woff"
    subset_font(args.input_font, woff_subset, "woff")

    # Generate CSS
    generate_css(args.output_dir, args.cdn_url)

    # Upload to S3/CloudFront if requested
    if not args.skip_upload and args.s3_bucket:
        print("\nUploading to S3/CloudFront...")
        upload_fonts_to_cloudfront(args.output_dir, args.s3_bucket, args.s3_prefix)

    print("\n" + "=" * 60)
    print("✓ Font subsetting complete!")
    print("=" * 60)
    print(f"\nGenerated files in: {args.output_dir}")
    print("  - mplantin-subset.woff2 (recommended)")
    print("  - mplantin-subset.woff (fallback)")
    print("  - mplantin-subset.css")

    if args.s3_bucket and not args.skip_upload:
        print(f"\nUploaded to: s3://{args.s3_bucket}/{args.s3_prefix}/")
        print(f"CDN URL: {args.cdn_url}/")


if __name__ == "__main__":
    main()
