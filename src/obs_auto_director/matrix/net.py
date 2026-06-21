from __future__ import annotations

import socket
import time
import urllib.request

"""Network info so listener inputs can advertise where callers connect.

LAN addresses are for same-network callers; the public IP is for callers across
the internet (which also requires port forwarding / firewall rules on the SRT
port).
"""

_PUBLIC_IP_SERVICES = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

_public_ip_cache: tuple[float, str | None] = (0.0, None)
_PUBLIC_IP_TTL_SEC = 300.0


def public_ip() -> str | None:
    """Best-effort public IP via an external service. Cached, fails to None."""
    global _public_ip_cache
    cached_at, cached_value = _public_ip_cache
    if cached_value is not None and time.time() - cached_at < _PUBLIC_IP_TTL_SEC:
        return cached_value

    for url in _PUBLIC_IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=2.5) as response:
                value = response.read().decode("utf-8").strip()
                if value:
                    _public_ip_cache = (time.time(), value)
                    return value
        except Exception:  # noqa: BLE001 - offline or blocked; try next/none
            continue
    return None


def primary_ip() -> str:
    """Best-guess LAN IP of this host.

    Opens a UDP socket toward a public address to discover which local interface
    the OS would route through. No packets are actually sent.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def local_ip_addresses() -> list[str]:
    """All IPv4 addresses for this host, primary first, loopback last."""
    addresses: list[str] = []
    primary = primary_ip()
    addresses.append(primary)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass

    if "127.0.0.1" not in addresses:
        addresses.append("127.0.0.1")
    return addresses
