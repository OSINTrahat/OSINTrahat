"""Shared fakes. No network, no real credentials: these keys are the public
example values from the Binance API documentation."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from luna_binance_manager.client import BinanceClient

DOC_KEY = "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"
DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"


class FakeTransport:
    """Records requests; replies from a route table or raises a queued exception."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {}

    def route(self, method: str, path: str, response: Any, status: int = 200) -> None:
        self.routes[(method.upper(), path)] = (status, response)

    def __call__(self, method, url, headers, body, timeout):
        parts = urlsplit(url)
        call = {"method": method, "host": parts.hostname, "path": parts.path,
                "query": parse_qs(parts.query), "headers": dict(headers),
                "body": parse_qs(body.decode()) if body else {}, "raw_url": url,
                "raw_body": body.decode() if body else "", "timeout": timeout}
        self.calls.append(call)
        status, resp = self.routes.get((method, parts.path), (404, {"code": -1, "msg": "no route"}))
        if isinstance(resp, Exception):
            raise resp
        if callable(resp):
            resp = resp(call)
        return status, json.dumps(resp)


def make_client(transport: FakeTransport | None = None, host: str = "api.binance.com") -> tuple[BinanceClient, FakeTransport]:
    t = transport or FakeTransport()
    c = BinanceClient(DOC_KEY, DOC_SECRET, host, transport=t, clock=lambda: 1499827319.559)
    return c, t


BTCUSDT_INFO = {
    "symbols": [{
        "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.01000000", "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": "0.00001000"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "100.00000000", "stepSize": "0.00000000"},
            {"filterType": "NOTIONAL", "minNotional": "5.00000000", "applyMinToMarket": True,
             "maxNotional": "9000000.00000000", "applyMaxToMarket": False, "avgPriceMins": 5},
        ],
    }]
}

COINS = [{
    "coin": "USDT", "name": "TetherUS", "free": "100.5", "withdrawAllEnable": True,
    "networkList": [
        {"network": "TRX", "name": "Tron (TRC20)", "withdrawEnable": True, "isDefault": True,
         "withdrawFee": "1", "withdrawMin": "10", "withdrawMax": "10000000", "withdrawIntegerMultiple": "0.000001",
         "addressRegex": "^T[1-9A-HJ-NP-Za-km-z]{33}$", "memoRegex": "", "withdrawTag": False, "busy": False},
        {"network": "ETH", "name": "Ethereum (ERC20)", "withdrawEnable": False, "isDefault": False,
         "withdrawFee": "3", "withdrawMin": "10", "withdrawMax": "0", "withdrawIntegerMultiple": "0.00000001",
         "addressRegex": "^(0x)[0-9A-Fa-f]{40}$", "memoRegex": "", "withdrawTag": False, "busy": False},
    ],
}, {
    "coin": "XRP", "name": "Ripple", "free": "50", "withdrawAllEnable": True,
    "networkList": [
        {"network": "XRP", "name": "Ripple", "withdrawEnable": True, "isDefault": True,
         "withdrawFee": "0.2", "withdrawMin": "1", "withdrawMax": "0", "withdrawIntegerMultiple": "0.000001",
         "addressRegex": "^r[1-9A-HJ-NP-Za-km-z]{24,34}$", "memoRegex": "^((?!0)[0-9]{1,10})$", "withdrawTag": True, "busy": False},
    ],
}]
TRX_ADDR = "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf"
XRP_ADDR = "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh"
