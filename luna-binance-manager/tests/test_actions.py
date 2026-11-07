import threading

import pytest

from luna_binance_manager.actions import ActionController
from luna_binance_manager.errors import ActionBlocked, BinanceAPIError, UnknownStatus
from luna_binance_manager.orders import SymbolRules, prepare_order
from luna_binance_manager.withdrawals import parse_coins, prepare_withdrawal

from fakes import BTCUSDT_INFO, COINS, TRX_ADDR, make_client

RULES = SymbolRules.from_exchange_info(BTCUSDT_INFO, "BTCUSDT")


def order(cid="cid"):
    return prepare_order(RULES, "BUY", "LIMIT", quantity="0.001", price="50000", client_order_id=cid)


def withdrawal(wid="wid"):
    return prepare_withdrawal(parse_coins(COINS)["USDT"], "TRX", TRX_ADDR, "", "20", withdraw_order_id=wid)


def test_read_only_by_default_blocks_everything():
    c, t = make_client()
    a = ActionController(c)
    assert a.read_only
    with pytest.raises(ActionBlocked, match="disabled"):
        a.place_order(order(), confirmed=True)
    with pytest.raises(ActionBlocked, match="disabled"):
        a.cancel_order("BTCUSDT", 1, confirmed=True)
    with pytest.raises(ActionBlocked, match="disabled"):
        a.withdraw(withdrawal(), confirmed=True)
    assert t.calls == []


def test_confirmation_required_even_when_enabled():
    c, t = make_client()
    a = ActionController(c)
    a.enable_trading(True)
    a.enable_withdrawals(True)
    with pytest.raises(ActionBlocked, match="confirmation"):
        a.place_order(order(), confirmed=False)
    with pytest.raises(ActionBlocked, match="confirmation"):
        a.withdraw(withdrawal(), confirmed=False)
    assert t.calls == []


def test_trading_enabled_does_not_enable_withdrawals():
    c, t = make_client()
    a = ActionController(c)
    a.enable_trading(True)
    with pytest.raises(ActionBlocked):
        a.withdraw(withdrawal(), confirmed=True)
    assert t.calls == []


def test_place_cancel_withdraw_happy_path():
    c, t = make_client()
    t.route("POST", "/api/v3/order", {"orderId": 42, "status": "NEW"})
    t.route("DELETE", "/api/v3/order", {"orderId": 42, "status": "CANCELED"})
    t.route("POST", "/sapi/v1/capital/withdraw/apply", {"id": "abc"})
    a = ActionController(c)
    a.enable_trading(True)
    a.enable_withdrawals(True)
    assert a.place_order(order(), confirmed=True)["orderId"] == 42
    assert t.calls[-1]["body"]["newClientOrderId"] == ["cid"]
    assert a.cancel_order("BTCUSDT", 42, confirmed=True)["status"] == "CANCELED"
    assert t.calls[-1]["method"] == "DELETE" and t.calls[-1]["body"]["orderId"] == ["42"]
    assert a.withdraw(withdrawal(), confirmed=True)["id"] == "abc"
    b = t.calls[-1]["body"]
    assert b["withdrawOrderId"] == ["wid"] and b["address"] == [TRX_ADDR] and "addressTag" not in b
    assert a.in_flight is None and a.unresolved() == []


def test_double_submission_is_blocked_while_in_flight():
    c, t = make_client()
    gate = threading.Event()
    second: dict = {}

    def slow(call):
        a2 = a
        try:
            a2.place_order(order("other"), confirmed=True)
        except ActionBlocked as e:
            second["err"] = str(e)
        gate.set()
        return {"orderId": 1, "status": "NEW"}
    t.route("POST", "/api/v3/order", slow)
    a = ActionController(c)
    a.enable_trading(True)
    a.place_order(order(), confirmed=True)
    assert gate.is_set() and "in progress" in second["err"]
    assert len(t.calls) == 1


def test_timeout_marks_unknown_blocks_and_reconciles_without_resubmitting():
    c, t = make_client()
    t.route("POST", "/api/v3/order", TimeoutError("slow"))
    a = ActionController(c)
    a.enable_trading(True)
    with pytest.raises(UnknownStatus):
        a.place_order(order("cid-x"), confirmed=True)
    assert len(t.calls) == 1  # no automatic retry
    assert len(a.unresolved()) == 1
    with pytest.raises(ActionBlocked, match="UNKNOWN"):
        a.place_order(order("cid-y"), confirmed=True)
    a.enable_withdrawals(True)
    with pytest.raises(ActionBlocked, match="UNKNOWN"):
        a.withdraw(withdrawal(), confirmed=True)
    assert len(t.calls) == 1

    t.route("GET", "/api/v3/order", {"orderId": 7, "status": "FILLED", "clientOrderId": "cid-x"})
    done = a.reconcile()
    assert t.calls[-1]["method"] == "GET" and t.calls[-1]["query"]["origClientOrderId"] == ["cid-x"]
    assert done[0].resolution == "order exists: orderId=7 status=FILLED"
    assert a.unresolved() == []
    t.route("POST", "/api/v3/order", {"orderId": 8, "status": "NEW"})
    assert a.place_order(order("cid-y"), confirmed=True)["orderId"] == 8


def test_server_error_and_minus_1007_are_unknown():
    c, t = make_client()
    a = ActionController(c)
    a.enable_withdrawals(True)
    t.route("POST", "/sapi/v1/capital/withdraw/apply", {"msg": "gateway"}, status=504)
    with pytest.raises(UnknownStatus):
        a.withdraw(withdrawal("w1"), confirmed=True)
    a.unknown.clear()
    t.route("POST", "/sapi/v1/capital/withdraw/apply", {"code": -1007, "msg": "Timeout waiting for response"}, status=200)
    with pytest.raises(UnknownStatus):
        a.withdraw(withdrawal("w2"), confirmed=True)
    assert [u.withdraw_order_id for u in a.unresolved()] == ["w2"]
    # reconcile: history says nothing was submitted -> resolved, resubmission allowed
    t.route("GET", "/sapi/v1/capital/withdraw/history", [])
    done = a.reconcile()
    assert t.calls[-1]["query"]["withdrawOrderId"] == ["w2"]
    assert "NOT submitted" in done[0].resolution and a.unresolved() == []


def test_ordinary_api_error_is_not_unknown_and_does_not_block():
    c, t = make_client()
    a = ActionController(c)
    a.enable_trading(True)
    t.route("POST", "/api/v3/order", {"code": -2010, "msg": "Account has insufficient balance"}, status=400)
    with pytest.raises(BinanceAPIError, match="insufficient"):
        a.place_order(order(), confirmed=True)
    assert a.unresolved() == [] and a.in_flight is None


def test_reconcile_keeps_unresolved_on_network_failure():
    c, t = make_client()
    a = ActionController(c)
    a.enable_trading(True)
    t.route("POST", "/api/v3/order", TimeoutError("slow"))
    with pytest.raises(UnknownStatus):
        a.place_order(order(), confirmed=True)
    t.route("GET", "/api/v3/order", ConnectionError("down"))
    assert a.reconcile() == [] and len(a.unresolved()) == 1
    t.route("GET", "/api/v3/order", {"code": -2013, "msg": "Order does not exist."}, status=400)
    done = a.reconcile()
    assert "NOT placed" in done[0].resolution
