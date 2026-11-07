"""HMAC-SHA256 request signing per Binance spot REST docs.

Payload = parameters as `k=v` joined by `&` (query string and body concatenated
without separator). Non-ASCII is percent-encoded before signing. Signature is a
hex digest sent as the `signature` parameter.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping
from urllib.parse import quote, urlencode


def encode_params(params: Mapping[str, Any]) -> str:
    """Encode params in insertion order, dropping None values."""
    items = [(k, _fmt(v)) for k, v in params.items() if v is not None]
    return urlencode(items, quote_via=quote, safe="")


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def hmac_sha256_hex(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_payload(secret: str, params: Mapping[str, Any]) -> str:
    """Return the encoded payload with the `signature` parameter appended."""
    payload = encode_params(params)
    return payload + "&signature=" + hmac_sha256_hex(secret, payload)
