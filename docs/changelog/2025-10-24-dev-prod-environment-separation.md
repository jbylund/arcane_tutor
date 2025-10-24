# Development and Production Environment Separation

**Date:** 2025-10-24

## Overview

This change introduces proper separation between development and production environments for the Arcane Tutor application, making it easier to run and test the application locally without interfering with production configurations.

## Changes

### 1. Separate Docker Compose Files

Created two new docker-compose files to separate dev and prod configurations:

- `docker-compose.dev.yml` - Development environment configuration
- `docker-compose.prod.yml` - Production environment configuration

Both files include the `ENV` environment variable set to their respective values (`dev` or `prod`).

### 2. Port Configuration

To avoid port conflicts when running both dev and prod simultaneously:

**Development Mode** (port + 10000):
- PostgreSQL: 25432 (host) → 5432 (container)
- API Service: 28080 (host) → 8080 (container)

**Production Mode** (original ports):
- PostgreSQL: 15432 (host) → 5432 (container)
- API Service: 18080 (host) → 8080 (container)

### 3. Makefile Targets

Added new make targets to support both environments:

**Starting Services:**
- `make up` - Default, starts development mode (alias for `up-dev`)
- `make up-dev` - Start services in development mode
- `make up-prod` - Start services in production mode
- `make up-detach` - Default detached mode (alias for `up-detach-dev`)
- `make up-detach-dev` - Start dev services in detached mode
- `make up-detach-prod` - Start prod services in detached mode

**Stopping Services:**
- `make down` - Default, stops development services (alias for `down-dev`)
- `make down-dev` - Stop development services
- `make down-prod` - Stop production services

**Building Images:**
- `make build_images_dev` - Build dev images
- `make build_images_prod` - Build prod images
- `make images_dev` - Build and pull dev images
- `make images_prod` - Build and pull prod images

**Database Connection:**
- `make dbconn` - Connect to production database (port 15432)
- `make dbconn-dev` - Connect to development database (port 25432)

### 4. Environment Variable Integration

The `ENV` environment variable is now:
- Set to `dev` in development mode
- Set to `prod` in production mode
- Passed through to all containers (postgres, apiservice, client)
- Used by Honeybadger error monitoring to tag errors by environment

### 5. Honeybadger Configuration

Updated `api/utils/error_monitoring.py` to read the `ENV` environment variable and pass it to Honeybadger's `environment` configuration parameter. This allows error tracking to be properly segmented by environment.

Default value is `development` if `ENV` is not set.

## Usage Examples

### Running in Development Mode (Default)

```bash
# Start all services in development mode
make up

# Or explicitly
make up-dev

# Start in detached mode
make up-detach
```

### Running in Production Mode

```bash
# Start all services in production mode
make up-prod

# Start in detached mode
make up-detach-prod
```

### Accessing Services

**Development Mode:**
- API: http://localhost:28080
- PostgreSQL: localhost:25432

**Production Mode:**
- API: http://localhost:18080
- PostgreSQL: localhost:15432

### Connecting to Database

```bash
# Connect to dev database
make dbconn-dev

# Connect to prod database
make dbconn
```

## Testing

Added comprehensive tests in `api/tests/test_error_monitoring.py` to verify:
- ENV variable is correctly passed to Honeybadger when set to `prod`
- ENV variable is correctly passed to Honeybadger when set to `dev`
- Default value of `development` is used when ENV is not set

All 694 existing tests continue to pass with these changes.

## Migration Notes

- **Breaking Change**: `make up` now starts development mode instead of using the base docker-compose.yml
- Use `make up-prod` to start production mode
- The original `docker-compose.yml` is still present but not used by default
- Port assignments have changed for development mode to avoid conflicts

## Benefits

1. **Easier Local Development**: Dev mode uses different ports, allowing developers to run both environments simultaneously
2. **Better Error Tracking**: Honeybadger errors are now tagged by environment
3. **Clear Separation**: Explicit dev/prod targets make it clear which environment is being used
4. **Port Conflict Avoidance**: Development mode ports are offset by 10000 to avoid conflicts
5. **Consistent Workflow**: All services (postgres, api, client) use the same ENV variable
