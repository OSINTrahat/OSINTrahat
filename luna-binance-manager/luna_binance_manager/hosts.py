"""Explicit allowlist of official Binance REST hosts.

Sources: binance/binance-spot-api-docs rest-api.md (base endpoints) and the
official Spot Testnet (testnet.binance.vision). The testnet host has no /sapi
wallet endpoints, so wallet features are disabled when it is selected.
"""
from __future__ import annotations

LIVE_HOSTS: frozenset[str] = frozenset({
    "api.binance.com",
    "api-gcp.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
})
TESTNET_HOSTS: frozenset[str] = frozenset({"testnet.binance.vision"})
ALLOWED_HOSTS: frozenset[str] = LIVE_HOSTS | TESTNET_HOSTS

DEFAULT_LIVE_HOST = "api.binance.com"
DEFAULT_TESTNET_HOST = "testnet.binance.vision"


class HostNotAllowed(ValueError):
    pass


def check_host(host: str) -> str:
    """Return the normalised host or raise HostNotAllowed."""
    h = (host or "").strip().lower()
    if h not in ALLOWED_HOSTS:
        raise HostNotAllowed(f"host is not an official Binance API host: {h!r}")
    return h


def supports_wallet(host: str) -> bool:
    return check_host(host) in LIVE_HOSTS
