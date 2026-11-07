from decimal import Decimal

import pytest

from luna_binance_manager.errors import ValidationError
from luna_binance_manager.orders import SymbolRules, fmt, new_client_order_id, parse_symbol, prepare_order

from fakes import BTCUSDT_INFO

RULES = SymbolRules.from_exchange_info(BTCUSDT_INFO, "BTCUSDT")


def test_fmt_is_plain_decimal():
    assert fmt(Decimal("1E+2")) == "100"
    assert fmt(Decimal("0.00100000")) == "0.001"
    assert fmt(Decimal("5")) == "5"


def test_limit_order_passes_filters_and_serialises():
    o = prepare_order(RULES, "buy", "limit", quantity="0.00100", price="50000.00", client_order_id="cid1")
    assert o.to_params() == {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
                             "quantity": "0.001", "price": "50000", "newClientOrderId": "cid1"}
    assert o.estimated_notional == Decimal("50")


@pytest.mark.parametrize("kw,msg", [
    (dict(quantity="0.001", price="50000.005"), "multiple of 0.01"),
    (dict(quantity="0.000011", price="50000"), "multiple of 0.00001"),
    (dict(quantity="0.000001", price="50000"), "below minimum"),
    (dict(quantity="0.00001", price="50000"), "below minimum notional"),
    (dict(quantity="10000", price="50000"), "above maximum"),
    (dict(quantity="-1", price="50000"), "positive"),
    (dict(quantity="abc", price="50000"), "not a valid number"),
    (dict(quantity="0.001"), "need quantity and price"),
    (dict(quantity="0.001", price="1", quote_qty="5"), "do not accept quote"),
])
def test_limit_order_rejections(kw, msg):
    with pytest.raises(ValidationError, match=msg):
        prepare_order(RULES, "BUY", "LIMIT", **kw)


def test_market_orders():
    o = prepare_order(RULES, "SELL", "MARKET", quantity="0.001", reference_price=Decimal("60000"))
    assert o.to_params()["quantity"] == "0.001" and "price" not in o.to_params()
    o = prepare_order(RULES, "BUY", "MARKET", quote_qty="20", reference_price=Decimal("60000"))
    assert o.to_params()["quoteOrderQty"] == "20"
    with pytest.raises(ValidationError, match="below minimum notional"):
        prepare_order(RULES, "BUY", "MARKET", quantity="0.00001", reference_price=Decimal("60000"))
    with pytest.raises(ValidationError, match="exactly one"):
        prepare_order(RULES, "BUY", "MARKET", quantity="1", quote_qty="1", reference_price=Decimal("1"))
    with pytest.raises(ValidationError, match="reference price"):
        prepare_order(RULES, "BUY", "MARKET", quantity="0.001")
    with pytest.raises(ValidationError, match="above maximum"):
        prepare_order(RULES, "BUY", "MARKET", quantity="200", reference_price=Decimal("1"))


def test_symbol_not_trading_or_unknown():
    info = {"symbols": [dict(BTCUSDT_INFO["symbols"][0], status="BREAK")]}
    with pytest.raises(ValidationError, match="not trading"):
        prepare_order(SymbolRules.from_exchange_info(info, "BTCUSDT"), "BUY", "LIMIT", quantity="0.001", price="1")
    with pytest.raises(ValidationError, match="not found"):
        SymbolRules.from_exchange_info(BTCUSDT_INFO, "ETHUSDT")
    with pytest.raises(ValidationError):
        parse_symbol("btc/usdt")
    assert parse_symbol(" btcusdt ") == "BTCUSDT"


def test_client_order_id_is_unique_and_valid():
    a, b = new_client_order_id(), new_client_order_id()
    assert a != b and len(a) <= 36 and a.startswith("luna-")
