from __future__ import annotations


class LunaError(Exception):
    """Base error. Messages are already redacted when raised by the client."""


class ValidationError(LunaError):
    pass


class NetworkError(LunaError):
    pass


class RequestTimeout(NetworkError):
    """The request timed out: execution status is UNKNOWN."""


class ServerError(LunaError):
    """HTTP 5XX: Binance says execution status is UNKNOWN."""

    def __init__(self, status: int, body: str = ""):
        super().__init__(f"Binance server error HTTP {status}: {body[:200]}")
        self.status = status


class BinanceAPIError(LunaError):
    def __init__(self, status: int, code: int | None, msg: str):
        super().__init__(f"Binance API error (HTTP {status}, code {code}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg

    @property
    def is_unknown_status(self) -> bool:
        # -1007 TIMEOUT: "Send status unknown; execution status unknown."
        return self.code == -1007


class UnknownStatus(LunaError):
    """An action's outcome is unknown; reconcile before resubmitting."""


class ActionBlocked(LunaError):
    pass
