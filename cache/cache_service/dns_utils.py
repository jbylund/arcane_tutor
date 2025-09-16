"""DNS utilities for bypassing Docker's internal DNS resolution."""

import contextlib
import logging
import socket

import cachetools
import dns.resolver

logger = logging.getLogger(__name__)

# DNS cache with 60-second TTL
dns_cache = cachetools.TTLCache(maxsize=1000, ttl=60)


def _is_ip(host: str) -> bool:
    """Check if a hostname is already an IP address."""
    for af in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(af, host)
            return True
        except OSError:
            pass
    return False


def _resolve_host(host: str, resolver: dns.resolver.Resolver) -> list[tuple[str, str]]:
    """Resolve a hostname using the custom DNS resolver."""
    addrs = []
    for rrtype in ("A", "AAAA"):
        try:
            for r in resolver.resolve(host, rrtype):
                addrs.append((rrtype, r.to_text()))
        except Exception as oops:
            msg = f"DNS resolution failed for {host} ({rrtype}): {oops}"
            raise socket.gaierror(msg) from oops
    return addrs


@contextlib.contextmanager
def custom_dns(nameservers: list[str], timeout: float = 2.0) -> None:
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

    def my_getaddrinfo(host: str, port: int, family: int = 0, sock_type: int = 0, proto: int = 0, flags: int = 0) -> list[tuple]:  # noqa: PLR0913
        """Custom getaddrinfo that uses external DNS servers."""
        if _is_ip(host):
            return orig_getaddrinfo(host, port, family, sock_type, proto, flags)

        try:
            addrs = dns_cache[host]
        except KeyError:
            addrs = _resolve_host(host, resolver)
            dns_cache[host] = addrs

        results = []
        # Reuse the OS for canonical tuple formatting:
        for rr, ip in addrs:
            fam = socket.AF_INET if rr == "A" else socket.AF_INET6
            results.extend(orig_getaddrinfo(ip, port, fam, sock_type, proto, flags))
        return results

    try:
        socket.getaddrinfo = my_getaddrinfo
        yield
    finally:
        socket.getaddrinfo = orig_getaddrinfo
