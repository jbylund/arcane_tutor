#!/usr/bin/env python3
"""Download and subset the Mana font to include only the symbols used in the application.

This script:
1. Downloads the Mana font from the official repository
2. Subsets it to include only the glyphs we actually use
3. Generates optimized CSS with font-display: swap

Requirements:
    pip install fonttools brotli requests boto3
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import logging
from pathlib import Path

import requests

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

logger = logging.getLogger(__name__)

# All the mana symbol classes used in index.html
USED_SYMBOLS = [
    # Basic colors
    "r", "g", "w", "u", "b", "c",

    # Numbers 0-16
    "0", "1", "2", "3", "4", "5",
    "6", "7", "8", "9", "10", "11",
    "12", "13", "14", "15", "16",

    # Variables
    "x", "y", "z",

    # Special symbols
    "tap", "untap", "energy", "phyrexian", "snow", "chaos", "pw", "infinity",

    # 2-color hybrid
    "wu", "ub", "br", "rg", "gw", "wb", "ur", "bg", "rw", "gu",

    # Generic hybrid (2/)
    "2w", "2u", "2b", "2r", "2g",

    # Phyrexian hybrid
    "wp", "up", "bp", "rp", "gp",

    # 3-color phyrexian
    "wup", "wbp", "ubp", "urp", "brp", "bgp", "rwp", "rgp", "gwp", "gup",
]

# Mana font repository
MANA_FONT_REPO = "https://github.com/andrewgioia/mana"
MANA_FONT_VERSION = "1.12.3"  # Latest stable version as of 2024


def check_dependencies() -> None:
    """Check if required tools are installed."""
    pyftsubset_path = shutil.which("pyftsubset")
    if not pyftsubset_path:
        sys.exit(1)
    try:
        subprocess.run([pyftsubset_path, "--help"], capture_output=True, check=True)  # noqa: S603
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(1)


def download_file(url: str, dest_path: Path) -> None:
    """Download a file from a URL to a destination path."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    dest_path.write_bytes(response.content)


def get_unicode_ranges() -> str:
    """Get the Unicode ranges or specific code points for the mana symbols.

    The Mana font uses the Private Use Area (PUA) of Unicode.
    We need to identify which specific glyphs to include.
    """
    # For subsetting, we'll use glyph names instead of Unicode ranges
    # The Mana font uses descriptive glyph names that match the CSS classes

    # Build a list of glyph patterns
    glyph_names = []
    for symbol in USED_SYMBOLS:
        # The font uses glyph names like "ms-w", "ms-u", etc.
        glyph_names.append(f"ms-{symbol}")

    return ",".join(glyph_names)


def subset_font(input_font: Path, output_font: Path, font_format: str = "woff2") -> None:
    """Subset the font to include only used glyphs."""
    # We'll use text subsetting - extract characters that the font uses for these symbols
    # The Mana font maps specific Unicode Private Use Area characters to each symbol

    # Alternative approach: keep specific glyphs by using --text with representative characters
    # For icon fonts, we often need to specify Unicode code points

    # Let's use a more inclusive approach: keep all basic Latin + the Private Use Area
    # that the font uses, filtered by the features we need

    pyftsubset_path = shutil.which("pyftsubset")
    if not pyftsubset_path:
        sys.exit(1)

    cmd = [
        pyftsubset_path,
        str(input_font),
        f"--output-file={output_font}",
        f"--flavor={font_format}",
        # Keep the essential tables
        "--layout-features=*",  # Keep all layout features
        "--glyph-names",  # Preserve glyph names
        "--symbol-cmap",  # Keep symbol character map
        "--legacy-cmap",  # Keep legacy character map
        "--notdef-outline",  # Keep .notdef glyph
        "--notdef-glyph",
        "--recommended-glyphs",
        # Unicode ranges for Private Use Area (where icon fonts typically live)
        "--unicodes=U+E600-E6FF,U+E900-E9FF",  # Common PUA ranges for icon fonts
        # Desubroutinize to reduce complexity
        "--desubroutinize",
    ]

    result = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603

    if result.returncode != 0:
        sys.exit(1)

    # Check file size
    input_size = input_font.stat().st_size
    output_size = output_font.stat().st_size
    reduction_percentage = (1 - output_size / input_size) * 100
    logger.info(f"Font subsetted successfully: {reduction_percentage:.2f}% reduction")


def generate_css(output_dir: Path, cdn_base_url: str = "/cdn/fonts/mana") -> None:
    """Generate the CSS file with @font-face declarations."""
    css_content = f"""/**
 * Mana Font - Optimized subset for card search
 * Contains only the symbols used in the application
 *
 * Original font: https://github.com/andrewgioia/mana
 * Version: {MANA_FONT_VERSION}
 */

@font-face {{
  font-family: "Mana";
  src: url('{cdn_base_url}/mana-subset.woff2') format('woff2'),
       url('{cdn_base_url}/mana-subset.woff') format('woff');
  font-weight: normal;
  font-style: normal;
  font-display: swap;
}}

/* Base mana symbol styles */
.ms {{
  display: inline-block;
  font: normal normal normal 14px Mana;
  font-size: inherit;
  line-height: 1em;
  text-rendering: auto;
  transform: translate(0, 0);
  speak: none;
  text-transform: none;
  vertical-align: middle;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  --ms-mana-b: #a7999e;
  --ms-mana-c: #d0c6bb;
  --ms-mana-g: #9fcba6;
  --ms-mana-r: #f19b79;
  --ms-mana-u: #bcdaf7;
  --ms-mana-w: #fdfbce;
}}

/* Individual symbol content codes */
.ms-w::before {{
  content: "\\e600";
}}
.ms-u::before {{
  content: "\\e601";
}}
.ms-b::before {{
  content: "\\e602";
}}
.ms-r::before {{
  content: "\\e603";
}}
.ms-g::before {{
  content: "\\e604";
}}
.ms-0::before {{
  content: "\\e605";
}}
.ms-1::before {{
  content: "\\e606";
}}
.ms-2 {{
  margin-left: inherit !important;
}}
.ms-2::before {{
  content: "\\e607";
}}
.ms-3::before {{
  content: "\\e608";
}}
.ms-4::before {{
  content: "\\e609";
}}
.ms-5::before {{
  content: "\\e60a";
}}
.ms-6::before {{
  content: "\\e60b";
}}
.ms-7::before {{
  content: "\\e60c";
}}
.ms-8::before {{
  content: "\\e60d";
}}
.ms-9::before {{
  content: "\\e60e";
}}
.ms-10::before {{
  content: "\\e60f";
}}
.ms-11::before {{
  content: "\\e610";
}}
.ms-12::before {{
  content: "\\e611";
}}
.ms-13::before {{
  content: "\\e612";
}}
.ms-14::before {{
  content: "\\e613";
}}
.ms-15::before {{
  content: "\\e614";
}}
.ms-16::before {{
  content: "\\e62a";
}}
.ms-x::before {{
  content: "\\e615";
}}
.ms-y::before {{
  content: "\\e616";
}}
.ms-z::before {{
  content: "\\e617";
}}
.ms-s::before {{
  content: "\\e619";
}}
.ms-c::before {{
  content: "\\e904";
}}
.ms-e::before, .ms-energy::before {{
  content: "\\e907";
}}
.ms-infinity::before {{
  content: "\\e903";
}}
.ms-p::before {{
  content: "\\e618";
}}
.ms-tap::before {{
  content: "\\e61a";
}}
.ms-untap::before {{
  content: "\\e61b";
}}
.ms-chaos::before {{
  content: "\\e61d";
}}
.ms-planeswalker::before {{
  content: "\\e623";
}}

/* Hybrid mana - uses ::before and ::after for split symbols */
.ms-wu::before, .ms-wb::before, .ms-rw::after, .ms-gw::after, .ms-2w::after {{
  content: "\\e600";
}}
.ms-ub::before, .ms-ur::before, .ms-wu::after, .ms-gu::after, .ms-2u::after {{
  content: "\\e601";
}}
.ms-br::before, .ms-bg::before, .ms-wb::after, .ms-ub::after, .ms-2b::after {{
  content: "\\e602";
}}
.ms-rw::before, .ms-rg::before, .ms-ur::after, .ms-br::after, .ms-2r::after {{
  content: "\\e603";
}}
.ms-gw::before, .ms-gu::before, .ms-bg::after, .ms-rg::after, .ms-2g::after {{
  content: "\\e604";
}}
.ms-2w::before, .ms-2u::before, .ms-2b::before, .ms-2r::before, .ms-2g::before {{
  content: "\\e607";
}}
.ms-wp::before, .ms-up::before, .ms-bp::before, .ms-rp::before, .ms-gp::before, .ms-wup::before, .ms-wup::after, .ms-wbp::before, .ms-wbp::after, .ms-ubp::before, .ms-ubp::after, .ms-urp::before, .ms-urp::after, .ms-brp::before, .ms-brp::after, .ms-bgp::before, .ms-bgp::after, .ms-rwp::before, .ms-rwp::after, .ms-rgp::before, .ms-rgp::after, .ms-gwp::before, .ms-gwp::after, .ms-gup::before, .ms-gup::after {{
  content: "\\e618";
}}

/* Cost styling */
.ms-cost {{
  background-color: #beb9b2;
  border-radius: 1em;
  color: #111;
  font-size: 0.95em;
  width: 1.3em;
  height: 1.3em;
  line-height: 1.35em;
  text-align: center;
}}
.ms-cost.ms-w, .ms-cost.ms-wp {{
  background-color: #f0f2c0;
}}
.ms-cost.ms-u, .ms-cost.ms-up {{
  background-color: #b5cde3;
}}
.ms-cost.ms-b, .ms-cost.ms-bp {{
  background-color: #aca29a;
}}
.ms-cost.ms-r, .ms-cost.ms-rp {{
  background-color: #db8664;
}}
.ms-cost.ms-g, .ms-cost.ms-gp {{
  background-color: #93b483;
}}
.ms-cost.ms-wu, .ms-cost.ms-wb, .ms-cost.ms-ub, .ms-cost.ms-ur, .ms-cost.ms-br, .ms-cost.ms-bg, .ms-cost.ms-rw, .ms-cost.ms-rg, .ms-cost.ms-gw, .ms-cost.ms-gu, .ms-cost.ms-2w, .ms-cost.ms-2u, .ms-cost.ms-2b, .ms-cost.ms-2r, .ms-cost.ms-2g, .ms-cost.ms-wup, .ms-cost.ms-wbp, .ms-cost.ms-ubp, .ms-cost.ms-urp, .ms-cost.ms-brp, .ms-cost.ms-bgp, .ms-cost.ms-rwp, .ms-cost.ms-rgp, .ms-cost.ms-gwp, .ms-cost.ms-gup {{
  --ms-split-top: var(--ms-mana-c);
  --ms-split-bottom: var(--ms-mana-u);
  background: var(--ms-split-top);
  background: -moz-linear-gradient(135deg, var(--ms-split-top) 0%, var(--ms-split-top) 50%, var(--ms-split-bottom) 50%, var(--ms-split-bottom) 100%);
  background: -webkit-linear-gradient(135deg, var(--ms-split-top) 0%, var(--ms-split-top) 50%, var(--ms-split-bottom) 50%, var(--ms-split-bottom) 100%);
  background: linear-gradient(135deg, var(--ms-split-top) 0%, var(--ms-split-top) 50%, var(--ms-split-bottom) 50%, var(--ms-split-bottom) 100%);
  position: relative;
  width: 1.3em;
  height: 1.3em;
}}
.ms-cost.ms-wu::before, .ms-cost.ms-wu::after, .ms-cost.ms-wb::before, .ms-cost.ms-wb::after, .ms-cost.ms-ub::before, .ms-cost.ms-ub::after, .ms-cost.ms-ur::before, .ms-cost.ms-ur::after, .ms-cost.ms-br::before, .ms-cost.ms-br::after, .ms-cost.ms-bg::before, .ms-cost.ms-bg::after, .ms-cost.ms-rw::before, .ms-cost.ms-rw::after, .ms-cost.ms-rg::before, .ms-cost.ms-rg::after, .ms-cost.ms-gw::before, .ms-cost.ms-gw::after, .ms-cost.ms-gu::before, .ms-cost.ms-gu::after, .ms-cost.ms-2w::before, .ms-cost.ms-2w::after, .ms-cost.ms-2u::before, .ms-cost.ms-2u::after, .ms-cost.ms-2b::before, .ms-cost.ms-2b::after, .ms-cost.ms-2r::before, .ms-cost.ms-2r::after, .ms-cost.ms-2g::before, .ms-cost.ms-2g::after, .ms-cost.ms-wup::before, .ms-cost.ms-wup::after, .ms-cost.ms-wbp::before, .ms-cost.ms-wbp::after, .ms-cost.ms-ubp::before, .ms-cost.ms-ubp::after, .ms-cost.ms-urp::before, .ms-cost.ms-urp::after, .ms-cost.ms-brp::before, .ms-cost.ms-brp::after, .ms-cost.ms-bgp::before, .ms-cost.ms-bgp::after, .ms-cost.ms-rwp::before, .ms-cost.ms-rwp::after, .ms-cost.ms-rgp::before, .ms-cost.ms-rgp::after, .ms-cost.ms-gwp::before, .ms-cost.ms-gwp::after, .ms-cost.ms-gup::before, .ms-cost.ms-gup::after {{
  font-size: 0.55em !important;
  position: absolute;
}}
.ms-cost.ms-wu::before, .ms-cost.ms-wb::before, .ms-cost.ms-ub::before, .ms-cost.ms-ur::before, .ms-cost.ms-br::before, .ms-cost.ms-bg::before, .ms-cost.ms-rw::before, .ms-cost.ms-rg::before, .ms-cost.ms-gw::before, .ms-cost.ms-gu::before, .ms-cost.ms-2w::before, .ms-cost.ms-2u::before, .ms-cost.ms-2b::before, .ms-cost.ms-2r::before, .ms-cost.ms-2g::before, .ms-cost.ms-wup::before, .ms-cost.ms-wbp::before, .ms-cost.ms-ubp::before, .ms-cost.ms-urp::before, .ms-cost.ms-brp::before, .ms-cost.ms-bgp::before, .ms-cost.ms-rwp::before, .ms-cost.ms-rgp::before, .ms-cost.ms-gwp::before, .ms-cost.ms-gup::before {{
  top: -0.38em;
  left: 0.28em;
}}
.ms-cost.ms-wu::after, .ms-cost.ms-wb::after, .ms-cost.ms-ub::after, .ms-cost.ms-ur::after, .ms-cost.ms-br::after, .ms-cost.ms-bg::after, .ms-cost.ms-rw::after, .ms-cost.ms-rg::after, .ms-cost.ms-gw::after, .ms-cost.ms-gu::after, .ms-cost.ms-2w::after, .ms-cost.ms-2u::after, .ms-cost.ms-2b::after, .ms-cost.ms-2r::after, .ms-cost.ms-2g::after, .ms-cost.ms-wup::after, .ms-cost.ms-wbp::after, .ms-cost.ms-ubp::after, .ms-cost.ms-urp::after, .ms-cost.ms-brp::after, .ms-cost.ms-bgp::after, .ms-cost.ms-rwp::after, .ms-cost.ms-rgp::after, .ms-cost.ms-gwp::after, .ms-cost.ms-gup::after {{
  top: 0.5em;
  left: 1em;
}}
.ms-cost.ms-wu, .ms-cost.ms-wup {{
  --ms-split-top: var(--ms-mana-w);
}}
.ms-cost.ms-wb, .ms-cost.ms-wbp {{
  --ms-split-top: var(--ms-mana-w);
  --ms-split-bottom: var(--ms-mana-b);
}}
.ms-cost.ms-ub, .ms-cost.ms-ubp {{
  --ms-split-top: var(--ms-mana-u);
  --ms-split-bottom: var(--ms-mana-b);
}}
.ms-cost.ms-ur, .ms-cost.ms-urp {{
  --ms-split-top: var(--ms-mana-u);
  --ms-split-bottom: var(--ms-mana-r);
}}
.ms-cost.ms-br, .ms-cost.ms-brp {{
  --ms-split-top: var(--ms-mana-b);
  --ms-split-bottom: var(--ms-mana-r);
}}
.ms-cost.ms-bg, .ms-cost.ms-bgp {{
  --ms-split-top: var(--ms-mana-b);
  --ms-split-bottom: var(--ms-mana-g);
}}
.ms-cost.ms-rw, .ms-cost.ms-rwp {{
  --ms-split-top: var(--ms-mana-r);
  --ms-split-bottom: var(--ms-mana-w);
}}
.ms-cost.ms-rg, .ms-cost.ms-rgp {{
  --ms-split-top: var(--ms-mana-r);
  --ms-split-bottom: var(--ms-mana-g);
}}
.ms-cost.ms-gw, .ms-cost.ms-gwp {{
  --ms-split-top: var(--ms-mana-g);
  --ms-split-bottom: var(--ms-mana-w);
}}
.ms-cost.ms-gu, .ms-cost.ms-gup {{
  --ms-split-top: var(--ms-mana-g);
}}
.ms-cost.ms-2w {{
  --ms-split-bottom: var(--ms-mana-w);
}}
.ms-cost.ms-2b {{
  --ms-split-bottom: var(--ms-mana-b);
}}
.ms-cost.ms-2r {{
  --ms-split-bottom: var(--ms-mana-r);
}}
.ms-cost.ms-2g {{
  --ms-split-bottom: var(--ms-mana-g);
}}
.ms-cost.ms-p::before {{
  display: inline-block;
  -moz-transform: scale(1.2, 1.2);
  -webkit-transform: scale(1.2, 1.2);
  transform: scale(1.2, 1.2);
}}
.ms-cost.ms-wp::before, .ms-cost.ms-up::before, .ms-cost.ms-bp::before, .ms-cost.ms-rp::before, .ms-cost.ms-gp::before, .ms-cost.ms-wup::before, .ms-cost.ms-wbp::before, .ms-cost.ms-ubp::before, .ms-cost.ms-urp::before, .ms-cost.ms-brp::before, .ms-cost.ms-bgp::before, .ms-cost.ms-rwp::before, .ms-cost.ms-rgp::before, .ms-cost.ms-gwp::before, .ms-cost.ms-gup::before, .ms-cost.ms-wup::after, .ms-cost.ms-wbp::after, .ms-cost.ms-ubp::after, .ms-cost.ms-urp::after, .ms-cost.ms-brp::after, .ms-cost.ms-bgp::after, .ms-cost.ms-rwp::after, .ms-cost.ms-rgp::after, .ms-cost.ms-gwp::after, .ms-cost.ms-gup::after {{
  display: inline-block;
  transform: scale(1.2) translateX(0.01rem) translateY(-0.03rem);
}}
.ms-cost.ms-s::before {{
  color: #fff;
  -webkit-text-stroke: 2px #fff;
  font-size: 0.85em;
  top: -0.05em;
  position: relative;
  display: inline-block;
}}
.ms-cost.ms-s::after {{
  content: "\\e619";
  position: absolute;
  color: #333;
  margin-left: -0.9em;
  font-size: 1.1em;
}}
.ms-cost.ms-untap {{
  background-color: #111;
  color: #fff;
}}
.ms-cost.ms-shadow {{
  box-shadow: -0.06em 0.07em 0 #111, 0 0.06em 0 #111;
}}
.ms-cost.ms-shadow.ms-untap {{
  box-shadow: -0.06em 0.07em 0 #fff, 0 0.06em 0 #fff;
}}

/* Split mana positioning */
.ms-split {{
  position: relative;
  width: 1.3em;
  height: 1.3em;
}}

.ms-split::before,
.ms-split::after {{
  font-size: 0.55em !important;
  position: absolute;
}}

.ms-split::before {{
  top: -0.38em;
  left: 0.28em;
}}

.ms-split::after {{
  top: 0.5em;
  left: 1em;
}}
"""

    css_path = output_dir / "mana-subset.css"
    css_path.write_text(css_content)


def configure_bucket_cors(bucket: str) -> bool:
    """Configure CORS on the S3 bucket to allow font loading."""
    if not HAS_BOTO3:
        return False

    try:
        s3_client = boto3.client("s3")

        cors_configuration = {
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

        s3_client.put_bucket_cors(
            Bucket=bucket,
            CORSConfiguration=cors_configuration,
        )

        return True
    except ClientError:
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
        return False

    try:
        s3_client = boto3.client("s3")


        with file_path.open("rb") as f:
            # Don't set ACL - rely on bucket policy for public access
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=f,
                ContentType=content_type,
                CacheControl=cache_control,
            )

        return True

    except NoCredentialsError:
        return False
    except ClientError:
        return False


def upload_fonts_to_cloudfront(
    output_dir: Path,
    bucket: str,
    s3_prefix: str = "cdn/fonts/mana",
) -> None:
    """Upload all font files to S3/CloudFront with proper headers."""
    if not HAS_BOTO3:
        return

    # Configure CORS on the bucket first
    if configure_bucket_cors(bucket):
        pass
    else:
        pass

    # Ensure s3_prefix doesn't start with / but does end without /
    s3_prefix = s3_prefix.strip("/")

    # Files to upload with their content types
    files_to_upload = [
        ("mana-subset.woff2", "font/woff2"),
        ("mana-subset.woff", "font/woff"),
        ("mana-subset.css", "text/css; charset=utf-8"),
    ]

    for filename, content_type in files_to_upload:
        file_path = output_dir / filename
        if not file_path.exists():
            continue

        s3_key = f"{s3_prefix}/{filename}"

        upload_to_s3(file_path, bucket, s3_key, content_type)


def load_env() -> None:
    """Load environment variables from env.json."""
    env_path = Path("env.json")
    with env_path.open(encoding="utf-8") as f:
        env = json.load(f)
    for key, value in env.items():
        os.environ[key] = value


def main() -> None:
    """Main entry point for the font subsetting tool."""
    parser = argparse.ArgumentParser(
        description="Subset the Mana font for optimal loading",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/fonts/mana"),
        help="Output directory for font files (default: data/fonts/mana)",
    )
    parser.add_argument(
        "--cdn-url",
        type=str,
        default="https://d1hot9ps2xugbc.cloudfront.net/cdn/fonts/mana",
        help="CDN base URL for the font files",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading font, use existing files",
    )
    parser.add_argument(
        "--s3-bucket",
        type=str,
        help="S3 bucket name for CloudFront upload (e.g., your-bucket-name)",
    )
    parser.add_argument(
        "--s3-prefix",
        type=str,
        default="cdn/fonts/mana",
        help="S3 key prefix for uploaded files (default: cdn/fonts/mana)",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip S3 upload, only generate files locally",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    load_env()


    # Check dependencies
    check_dependencies()

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        if not args.skip_download:
            # Download the original font files
            woff2_url = "https://github.com/andrewgioia/mana/raw/master/fonts/mana.woff2"
            woff_url = "https://github.com/andrewgioia/mana/raw/master/fonts/mana.woff"

            woff2_orig = tmppath / "mana-original.woff2"
            woff_orig = tmppath / "mana-original.woff"

            try:
                download_file(woff2_url, woff2_orig)
                download_file(woff_url, woff_orig)
            except (OSError, requests.RequestException):
                sys.exit(1)
        else:
            woff2_orig = args.output_dir / "mana-original.woff2"
            woff_orig = args.output_dir / "mana-original.woff"

        # Subset to woff2
        woff2_subset = args.output_dir / "mana-subset.woff2"
        subset_font(woff2_orig, woff2_subset, "woff2")

        # Subset to woff
        woff_subset = args.output_dir / "mana-subset.woff"
        subset_font(woff_orig, woff_subset, "woff")

        # Generate CSS
        generate_css(args.output_dir, args.cdn_url)

        # Upload to S3/CloudFront if requested
        if not args.skip_upload and args.s3_bucket:
            upload_fonts_to_cloudfront(args.output_dir, args.s3_bucket, args.s3_prefix)


        if args.skip_upload or not args.s3_bucket:
            pass
        else:
            pass



if __name__ == "__main__":
    main()
