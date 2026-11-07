"""Scrub secrets from any text that could reach logs or dialogs."""
from __future__ import annotations


class Redactor:
    def __init__(self) -> None:
        self._secrets: list[str] = []

    def register(self, *values: str | None) -> None:
        for v in values:
            if v and len(v) >= 8 and v not in self._secrets:
                self._secrets.append(v)

    def clear(self) -> None:
        self._secrets.clear()

    def __call__(self, text: object) -> str:
        s = str(text)
        for v in self._secrets:
            s = s.replace(v, "[REDACTED]")
        return s


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
