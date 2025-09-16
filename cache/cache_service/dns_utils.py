"""DNS utilities for bypassing Docker's internal DNS resolution."""

import contextlib
import socket

import cachetools
import dns.resolver

# DNS cache with 60-second TTL
dns_cache = cachetools.TTLCache(maxsize=1000, ttl=60)


@contextlib.contextmanager
def custom_dns(nameservers: list[str], timeout: float = 2.0):
    """Temporarily override socket.getaddrinfo to use dnspython with external DNS servers.

    Args:
        nameservers: List of DNS server IP addresses to use
        timeout: DNS resolution timeout in seconds

    Yields:
        None: Context manager that overrides DNS resolution
    """
    orig_getaddrinfo = socket.getaddrinfo

    resolver = dns.resolver.Resolver(configure=True)
    resolver.nameservers = nameservers
    resolver.lifetime = timeout

    def _is_ip(host: str) -> bool:
        """Check if a hostname is already an IP address."""
        for af in (socket.AF_INET, socket.AF_INET6):
            try:
                socket.inet_pton(af, host)
                return True
            except OSError:
                pass
        return False

    def my_getaddrinfo(host: str, port: int, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
        """Custom getaddrinfo that uses external DNS servers."""
        if _is_ip(host):
            return orig_getaddrinfo(host, port, family, type, proto, flags)

        try:
            addrs = dns_cache[host]
        except KeyError:
            addrs = []
            for rrtype in ("A", "AAAA"):
                try:
                    for r in resolver.resolve(host, rrtype):
                        addrs.append((rrtype, r.to_text()))
                except Exception:
                    pass
            if not addrs:
                msg = f"custom resolver failed: {host}"
                raise socket.gaierror(msg)
            dns_cache[host] = addrs

        results = []
        # Reuse the OS for canonical tuple formatting:
        for rr, ip in addrs:
            fam = socket.AF_INET if rr == "A" else socket.AF_INET6
            results.extend(orig_getaddrinfo(ip, port, fam, type, proto, flags))
        return results

    try:
        socket.getaddrinfo = my_getaddrinfo
        yield
    finally:
        socket.getaddrinfo = orig_getaddrinfo
