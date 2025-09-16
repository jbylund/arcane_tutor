    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle GET requests to proxy and cache Scryfall API calls.

        Query parameters:
        - url: The Scryfall URL to fetch (required)
        - max_age: Maximum age of cached data in seconds (optional, default 3600)
        """
        logger.info("Getting data from cache or fetching from Scryfall: %s", req.uri)
        url = req.get_param("url", required=True)
        max_age = req.get_param_as_int("max_age", default=3600)

        # Validate that URL is from allowed domains
        allowed_hosts = {"api.scryfall.com", "scryfall.com"}
        parsed = urlparse(url)
        if parsed.netloc not in allowed_hosts:
            raise falcon.HTTPBadRequest(
                title="Invalid URL",
                description=f"Only URLs from {allowed_hosts} are allowed",
            )

        try:
            data = self.cache_service.get(url, max_age_seconds=max_age)
            resp.media = data
        except requests.RequestException as e:
            logger.error("Request failed for %s: %s", url, e)
            raise falcon.HTTPBadGateway(
                title="Upstream Error",
                description=f"Failed to fetch data from Scryfall: {e}",
            ) from e
        except ValueError as e:
            logger.error("Invalid response for %s: %s", url, e)
            raise falcon.HTTPBadRequest(
                title="Invalid Response",
                description=str(e),
            ) from e

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle POST requests to clear cache entries.

        Request body should contain:
        - url: The URL to clear from cache (optional, clears all if not provided)
        """
        try:
            data = req.get_media()
            url = data.get("url") if data else None
        except (ValueError, TypeError):
            url = None

        if url:
            # Clear specific URL from cache
            cache_path = self.cache_service._get_cache_path(url)
            if cache_path.exists():
                cache_path.unlink()
                resp.media = {"message": f"Cleared cache for {url}"}
            else:
                resp.media = {"message": f"No cache entry found for {url}"}
        # Clear all cache (remove all files in cache directory)
        elif self.cache_service.cache_dir.exists():
            shutil.rmtree(self.cache_service.cache_dir)
            self.cache_service.cache_dir.mkdir(parents=True, exist_ok=True)
            resp.media = {"message": "Cleared all cache"}
        else:
            resp.media = {"message": "Cache directory does not exist"}

    def on_get_stats(self, _req: falcon.Request, resp: falcon.Response) -> None:
        """Get cache statistics."""
        cache_dir = self.cache_service.cache_dir

        if not cache_dir.exists():
            resp.media = {
                "cache_entries": 0,
                "total_size_bytes": 0,
                "hosts": [],
            }
            return

        # Count files and calculate total size
        cache_entries = 0
        total_size = 0
        hosts = set()

        for host_dir in cache_dir.iterdir():
            if host_dir.is_dir():
                hosts.add(host_dir.name)
                for cache_file in host_dir.glob("*.json"):
                    if cache_file.is_file():
                        cache_entries += 1
                        total_size += cache_file.stat().st_size

        resp.media = {
            "cache_entries": cache_entries,
            "total_size_bytes": total_size,
            "hosts": sorted(hosts),
        }
