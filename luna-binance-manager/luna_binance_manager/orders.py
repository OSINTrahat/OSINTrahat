"""Decimal-based order validation against Binance symbol filters.

Filter semantics from binance/binance-spot-api-docs filters.md:
PRICE_FILTER (minPrice, maxPrice, tickSize), LOT_SIZE / MARKET_LOT_SIZE
(minQty, maxQty, stepSize), MIN_NOTIONAL (minNotional, applyToMarket),
NOTIONAL (minNotional, maxNotional, applyMinToMarket, applyMaxToMarket).
A rule whose value is 0 is disabled.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .errors import ValidationError

SIDES = ("BUY", "SELL")
ORDER_TYPES = ("LIMIT", "MARKET")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def parse_decimal(text: object, name: str) -> Decimal:
    s = str(text).strip()
    if not s:
        raise ValidationError(f"{name} is required")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValidationError(f"{name} is not a valid number: {s!r}") from None
    if not d.is_finite():
        raise ValidationError(f"{name} must be finite")
    return d


def fmt(d: Decimal) -> str:
    """Plain (non-scientific) string, trailing zeros stripped, at least '0'."""
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def is_multiple(value: Decimal, step: Decimal) -> bool:
    if step == 0:
        return True
    return (value % step) == 0


def parse_symbol(text: str) -> str:
    s = (text or "").strip().upper()
    if not _SYMBOL_RE.match(s):
        raise ValidationError("symbol must be like BTCUSDT (letters/digits only)")
    return s


@dataclass
class SymbolRules:
    symbol: str
    base: str
    quote: str
    status: str
    filters: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_exchange_info(cls, info: dict, symbol: str) -> "SymbolRules":
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                return cls(
                    symbol=symbol, base=s["baseAsset"], quote=s["quoteAsset"],
                    status=s.get("status", ""),
                    filters={f["filterType"]: f for f in s.get("filters", [])},
                )
        raise ValidationError(f"symbol {symbol} not found in exchangeInfo")

    def f(self, name: str) -> dict | None:
        return self.filters.get(name)


@dataclass(frozen=True)
class PreparedOrder:
    symbol: str
    side: str
    type: str
    quantity: Decimal | None
    quote_qty: Decimal | None
    price: Decimal | None
    client_order_id: str
    reference_price: Decimal | None = None

    @property
    def estimated_notional(self) -> Decimal | None:
        if self.type == "LIMIT" and self.price is not None and self.quantity is not None:
            return self.price * self.quantity
        if self.quote_qty is not None:
            return self.quote_qty
        if self.reference_price is not None and self.quantity is not None:
            return self.reference_price * self.quantity
        return None

    def to_params(self) -> dict:
        p: dict = {"symbol": self.symbol, "side": self.side, "type": self.type,
                   "newClientOrderId": self.client_order_id}
        if self.type == "LIMIT":
            p["timeInForce"] = "GTC"
            p["quantity"] = fmt(self.quantity)
            p["price"] = fmt(self.price)
        elif self.quote_qty is not None:
            p["quoteOrderQty"] = fmt(self.quote_qty)
        else:
            p["quantity"] = fmt(self.quantity)
        return p


def new_client_order_id(prefix: str = "luna") -> str:
    # newClientOrderId: documented regex ^[\.A-Z\:/a-z0-9_-]{1,36}$
    return (prefix + "-" + uuid.uuid4().hex)[:36]


def _check_range(value: Decimal, flt: dict, lo: str, hi: str, step: str, label: str) -> None:
    mn, mx, st = Decimal(flt.get(lo, "0")), Decimal(flt.get(hi, "0")), Decimal(flt.get(step, "0"))
    if mn > 0 and value < mn:
        raise ValidationError(f"{label} {fmt(value)} is below minimum {fmt(mn)}")
    if mx > 0 and value > mx:
        raise ValidationError(f"{label} {fmt(value)} is above maximum {fmt(mx)}")
    if st > 0 and not is_multiple(value, st):
        raise ValidationError(f"{label} {fmt(value)} must be a multiple of {fmt(st)}")


def prepare_order(rules: SymbolRules, side: str, order_type: str, *,
                  quantity: str | None = None, quote_qty: str | None = None,
                  price: str | None = None, reference_price: Decimal | None = None,
                  client_order_id: str | None = None) -> PreparedOrder:
    """Validate inputs with Decimal against symbol filters; return a PreparedOrder."""
    side = (side or "").upper()
    order_type = (order_type or "").upper()
    if side not in SIDES:
        raise ValidationError("side must be BUY or SELL")
    if order_type not in ORDER_TYPES:
        raise ValidationError("type must be LIMIT or MARKET")
    if rules.status != "TRADING":
        raise ValidationError(f"symbol {rules.symbol} is not trading (status {rules.status})")

    qty = parse_decimal(quantity, "quantity") if quantity not in (None, "") else None
    qqty = parse_decimal(quote_qty, "quote amount") if quote_qty not in (None, "") else None
    prc = parse_decimal(price, "price") if price not in (None, "") else None

    for name, v in (("quantity", qty), ("quote amount", qqty), ("price", prc)):
        if v is not None and v <= 0:
            raise ValidationError(f"{name} must be positive")

    if order_type == "LIMIT":
        if qty is None or prc is None:
            raise ValidationError("LIMIT orders need quantity and price")
        if qqty is not None:
            raise ValidationError("LIMIT orders do not accept quote amount")
        pf = rules.f("PRICE_FILTER")
        if pf:
            _check_range(prc, pf, "minPrice", "maxPrice", "tickSize", "price")
        ls = rules.f("LOT_SIZE")
        if ls:
            _check_range(qty, ls, "minQty", "maxQty", "stepSize", "quantity")
        notional = prc * qty
        _check_notional(rules, notional, market=False)
    else:
        if (qty is None) == (qqty is None):
            raise ValidationError("MARKET orders need exactly one of quantity or quote amount")
        if prc is not None:
            raise ValidationError("MARKET orders do not take a price")
        if qty is not None:
            ls = rules.f("LOT_SIZE")
            if ls:
                _check_range(qty, ls, "minQty", "maxQty", "stepSize", "quantity")
            mls = rules.f("MARKET_LOT_SIZE")
            if mls:
                _check_range(qty, mls, "minQty", "maxQty", "stepSize", "quantity")
            if reference_price is None:
                raise ValidationError("reference price is required to check MARKET notional")
            _check_notional(rules, reference_price * qty, market=True)
        else:
            _check_notional(rules, qqty, market=True)

    return PreparedOrder(
        symbol=rules.symbol, side=side, type=order_type, quantity=qty, quote_qty=qqty,
        price=prc, client_order_id=client_order_id or new_client_order_id(),
        reference_price=reference_price,
    )


def _check_notional(rules: SymbolRules, notional: Decimal, *, market: bool) -> None:
    n = rules.f("NOTIONAL")
    if n:
        mn, mx = Decimal(n.get("minNotional", "0")), Decimal(n.get("maxNotional", "0"))
        apply_min = (not market) or bool(n.get("applyMinToMarket", False))
        apply_max = (not market) or bool(n.get("applyMaxToMarket", False))
        if apply_min and mn > 0 and notional < mn:
            raise ValidationError(f"order value {fmt(notional)} is below minimum notional {fmt(mn)}")
        if apply_max and mx > 0 and notional > mx:
            raise ValidationError(f"order value {fmt(notional)} is above maximum notional {fmt(mx)}")
    m = rules.f("MIN_NOTIONAL")
    if m:
        mn = Decimal(m.get("minNotional", "0"))
        if ((not market) or bool(m.get("applyToMarket", False))) and mn > 0 and notional < mn:
            raise ValidationError(f"order value {fmt(notional)} is below minimum notional {fmt(mn)}")
