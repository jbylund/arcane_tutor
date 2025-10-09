# Copy Card Images to S3

This document describes the script for copying Magic: The Gathering card images from Scryfall to AWS S3 with WebP conversion and resizing.

## Overview

The `copy_images_to_s3.py` script fetches card data from the database, downloads high-quality PNG images from Scryfall, converts them to WebP format at three different sizes, and uploads them to S3.

## Prerequisites

### System Dependencies

- **cwebp**: WebP conversion tool
  - Ubuntu/Debian: `sudo apt-get install webp`
  - macOS: `brew install webp`

### Python Dependencies

- `boto3`: AWS S3 client
- `psycopg`: PostgreSQL database connection
- `requests`: HTTP client for downloading images

All Python dependencies are included in `requirements.txt`.

### AWS Credentials

The script uses boto3's default credential chain. Set up AWS credentials using one of these methods:

- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- AWS credentials file: `~/.aws/credentials`
- IAM role (when running on EC2)

### Database Connection

Set PostgreSQL connection parameters via environment variables:

- `PGHOST`: Database host
- `PGPORT`: Database port
- `PGUSER`: Database user
- `PGPASSWORD`: Database password
- `PGDATABASE`: Database name

## Image Sizes

The script generates three WebP versions of each card image:

| Size   | Width | Description                                 |
|--------|-------|---------------------------------------------|
| `lg`   | 745px | Full resolution (high quality)              |
| `med`  | ~404px| Medium resolution (sqrt(220 * 745))         |
| `sm`   | 220px | Small resolution (thumbnail)                |

All images are converted with:
- WebP quality: 85
- `-sharp_yuv` flag for sharpness preservation during downscaling

## S3 Storage Structure

Images are uploaded to S3 with the following key structure:

```
s3://biblioplex/{set_code}/{collector_number}/{size}.webp
```

Examples:
```
s3://biblioplex/iko/123/sm.webp
s3://biblioplex/iko/123/med.webp
s3://biblioplex/iko/123/lg.webp
s3://biblioplex/thb/42a/sm.webp
s3://biblioplex/thb/42a/med.webp
s3://biblioplex/thb/42a/lg.webp
```

## Usage

### Basic Usage

Process all cards with images in the database:

```bash
python -m scripts.copy_images_to_s3
```

### Filter by Set

Process only cards from a specific set:

```bash
python -m scripts.copy_images_to_s3 --set iko
```

### Limit Number of Cards

Process only the first N cards:

```bash
python -m scripts.copy_images_to_s3 --limit 100
```

### Different S3 Bucket

Upload to a different bucket:

```bash
python -m scripts.copy_images_to_s3 --bucket my-custom-bucket
```

### Re-process Existing Images

By default, the script skips cards that already have all three sizes in S3. To force re-processing:

```bash
python -m scripts.copy_images_to_s3 --no-skip-existing
```

### Dry Run

Test the script without actually downloading or uploading:

```bash
python -m scripts.copy_images_to_s3 --dry-run --limit 10
```

### Verbose Logging

Enable debug logging:

```bash
python -m scripts.copy_images_to_s3 --verbose
```

## Command-Line Options

```
--bucket BUCKET          S3 bucket name (default: biblioplex)
--set SET_CODE           Process only cards from a specific set
--limit LIMIT            Limit number of cards to process
--skip-existing          Skip cards with existing images (default: True)
--no-skip-existing       Re-process cards even if images exist
--dry-run                Don't actually download or upload
--verbose                Enable verbose logging
```

## Process Flow

1. **Database Query**: Fetch card data (set_code, collector_number, image_location_uuid)
2. **S3 Check**: If `--skip-existing`, check if all sizes already exist
3. **Download**: Fetch PNG image from Scryfall
4. **Convert**: Use cwebp to create 3 WebP versions at different sizes
5. **Upload**: Upload each size to S3 with proper content type
6. **Cleanup**: Temporary files are automatically cleaned up

## Performance Considerations

### Batch Processing

Process cards set by set to efficiently manage S3 checks:

```bash
# Process each set separately
for set in iko thb eld; do
    python -m scripts.copy_images_to_s3 --set $set
done
```

### Skipping Existing Images

The `--skip-existing` flag (default) performs a head request for each size before processing. To minimize S3 API calls when starting fresh, use `--no-skip-existing`:

```bash
python -m scripts.copy_images_to_s3 --no-skip-existing
```

### Parallel Processing

For large-scale processing, consider running multiple instances with different filters:

```bash
# Terminal 1
python -m scripts.copy_images_to_s3 --set iko

# Terminal 2
python -m scripts.copy_images_to_s3 --set thb

# Terminal 3
python -m scripts.copy_images_to_s3 --set eld
```

## Error Handling

The script handles various error conditions:

- **Missing cwebp**: Exits with installation instructions
- **Network failures**: Logs error and continues to next card
- **S3 upload failures**: Logs error and marks card as failed
- **Missing card data**: Skips cards with null values
- **Database connection issues**: Fails fast with clear error message

## Monitoring Progress

The script provides detailed logging:

```
INFO - Fetched 1234 cards from database
INFO - Processing card 1/1234
INFO - Processing iko/123
INFO - Completed iko/123: 3/3 sizes uploaded
INFO - Processing complete: 1200 successful, 34 failed out of 1234 total
```

## Testing

Run the test suite:

```bash
python -m pytest scripts/tests/test_copy_images_to_s3.py -v
```

## Examples

### Process a Single Set in Verbose Mode

```bash
python -m scripts.copy_images_to_s3 --set iko --verbose
```

### Dry Run on First 50 Cards

```bash
python -m scripts.copy_images_to_s3 --dry-run --limit 50 --verbose
```

### Re-process All Ikoria Cards

```bash
python -m scripts.copy_images_to_s3 --set iko --no-skip-existing
```

### Process with Custom Bucket

```bash
python -m scripts.copy_images_to_s3 --bucket my-images --limit 100
```

## Troubleshooting

### "cwebp not found"

Install webp tools:
- Ubuntu/Debian: `sudo apt-get install webp`
- macOS: `brew install webp`

### AWS Credentials Error

Ensure AWS credentials are configured:
```bash
aws configure
```

Or set environment variables:
```bash
export AWS_ACCESS_KEY_ID=your_key_id
export AWS_SECRET_ACCESS_KEY=your_secret_key
```

### Database Connection Error

Verify PostgreSQL environment variables:
```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=your_user
export PGPASSWORD=your_password
export PGDATABASE=your_database
```

### No Cards Found

Check that cards have non-null `image_location_uuid`:
```sql
SELECT COUNT(*) FROM magic.cards WHERE image_location_uuid IS NOT NULL;
```

## Future Enhancements

Potential improvements for the script:

- Parallel processing with multiprocessing
- Resume support for interrupted runs
- Integration with CDN invalidation
- Prometheus metrics for monitoring
- Support for double-faced cards (back images)
- Configurable image quality per size
- Progress bar using tqdm
