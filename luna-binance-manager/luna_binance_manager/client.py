"""Minimal HTTPS client for documented Binance REST endpoints.

- stdlib only (urllib + ssl); TLS verified with the system default context
- host must be on the explicit allowlist in hosts.py
- API key in `X-MBX-APIKEY` header; secret only ever used for HMAC in memory
- signed POST/DELETE parameters go in the form body, GET in the query string
- any text that leaves this module passes through the redactor
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from .errors import BinanceAPIError, NetworkError, RequestTimeout, ServerError
from .hosts import check_host
from .redact import Redactor
from .signing import encode_params, sign_payload

# transport(method, url, headers, body, timeout) -> (status, body_text)
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, str]]

USER_AGENT = "LunaBinanceManager/0.1 (local desktop; stdlib urllib)"


def _urllib_transport(method: str, url: str, headers: Mapping[str, str],
                      body: bytes | None, timeout: float) -> tuple[int, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except socket.timeout as e:  # pragma: no cover - depends on socket impl
        raise TimeoutError(str(e)) from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(e.reason)) from e
        raise ConnectionError(str(e.reason)) from e


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, host: str, *,
                 timeout: float = 10.0, recv_window: int = 5000,
                 transport: Transport | None = None, redactor: Redactor | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.host = check_host(host)
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        if not self._api_key or not self._api_secret:
            raise ValueError("API key and secret are required")
        self.timeout = timeout
        self.recv_window = recv_window
        self._transport = transport or _urllib_transport
        self.redact = redactor or Redactor()
        self.redact.register(self._api_key, self._api_secret)
        self._clock = clock
        self.time_offset_ms = 0

    # -- wiping ---------------------------------------------------------
    def close(self) -> None:
        self._api_key = ""
        self._api_secret = ""

    # -- low level ------------------------------------------------------
    def _timestamp(self) -> int:
        return int(self._clock() * 1000) + self.time_offset_ms

    def request(self, method: str, path: str, params: Mapping[str, Any] | None = None,
                *, signed: bool = False, api_key: bool = True) -> Any:
        method = method.upper()
        check_host(self.host)
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        p: dict[str, Any] = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key or signed:
            headers["X-MBX-APIKEY"] = self._api_key
        if signed:
            p["recvWindow"] = self.recv_window
            p["timestamp"] = self._timestamp()
            payload = sign_payload(self._api_secret, p)
        else:
            payload = encode_params(p)

        url = f"https://{self.host}{path}"
        body: bytes | None = None
        if method == "GET":
            if payload:
                url += "?" + payload
        else:
            body = payload.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            status, text = self._transport(method, url, headers, body, self.timeout)
        except TimeoutError as e:
            raise RequestTimeout(self.redact(f"request timed out after {self.timeout}s ({method} {path})")) from None
        except (ConnectionError, OSError) as e:
            raise NetworkError(self.redact(f"network error: {e}")) from None

        return self._handle(status, text, method, path)

    def _handle(self, status: int, text: str, method: str, path: str) -> Any:
        if status >= 500:
            raise ServerError(status, self.redact(text))
        data: Any
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            if status == 200:
                raise NetworkError(self.redact(f"non-JSON response from {path}"))
            data = {"msg": text}
        if status != 200:
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", "") if isinstance(data, dict) else str(data)
            raise BinanceAPIError(status, code, self.redact(msg))
        if isinstance(data, dict) and "code" in data and data.get("code") not in (0, 200, None) and "msg" in data:
            raise BinanceAPIError(status, data.get("code"), self.redact(data.get("msg", "")))
        return data

    # -- public (no key) -----------------------------------------------
    def server_time(self) -> int:
        return int(self.request("GET", "/api/v3/time", api_key=False)["serverTime"])

    def sync_time(self) -> int:
        local = int(self._clock() * 1000)
        self.time_offset_ms = self.server_time() - local
        return self.time_offset_ms

    def exchange_info(self, symbol: str) -> dict:
        return self.request("GET", "/api/v3/exchangeInfo", {"symbol": symbol}, api_key=False)

    def ticker_price(self, symbol: str) -> dict:
        return self.request("GET", "/api/v3/ticker/price", {"symbol": symbol}, api_key=False)

    # -- account (USER_DATA) -------------------------------------------
    def account(self) -> dict:
        return self.request("GET", "/api/v3/account", {"omitZeroBalances": True}, signed=True)

    def open_orders(self, symbol: str | None = None) -> list:
        return self.request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True)

    def my_trades(self, symbol: str, limit: int = 100) -> list:
        return self.request("GET", "/api/v3/myTrades", {"symbol": symbol, "limit": limit}, signed=True)

    def get_order(self, symbol: str, *, order_id: int | None = None,
                  orig_client_order_id: str | None = None) -> dict:
        return self.request("GET", "/api/v3/order", {
            "symbol": symbol, "orderId": order_id, "origClientOrderId": orig_client_order_id,
        }, signed=True)

    def api_restrictions(self) -> dict:
        return self.request("GET", "/sapi/v1/account/apiRestrictions", signed=True)

    # -- trade (TRADE) -------------------------------------------------
    def test_order(self, params: Mapping[str, Any]) -> dict:
        return self.request("POST", "/api/v3/order/test", params, signed=True)

    def new_order(self, params: Mapping[str, Any]) -> dict:
        return self.request("POST", "/api/v3/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self.request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    # -- wallet (USER_DATA, live hosts only) ---------------------------
    def coins_config(self) -> list:
        return self.request("GET", "/sapi/v1/capital/config/getall", signed=True)

    def withdraw_apply(self, params: Mapping[str, Any]) -> dict:
        return self.request("POST", "/sapi/v1/capital/withdraw/apply", params, signed=True)

    def withdraw_history(self, *, coin: str | None = None, withdraw_order_id: str | None = None,
                         limit: int = 100) -> list:
        return self.request("GET", "/sapi/v1/capital/withdraw/history", {
            "coin": coin, "withdrawOrderId": withdraw_order_id, "limit": limit,
        }, signed=True)
