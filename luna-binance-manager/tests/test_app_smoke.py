"""GUI smoke test: builds the window off-screen and drives connect/preview flows
with a fake transport. Skipped when no display is available."""
import os
import tkinter as tk

import pytest

from fakes import BTCUSDT_INFO, COINS, DOC_KEY, DOC_SECRET, FakeTransport

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY") and os.name != "nt", reason="no display")


@pytest.fixture
def app(monkeypatch):
    from luna_binance_manager import app as appmod
    t = FakeTransport()
    t.route("GET", "/api/v3/time", {"serverTime": 1499827319559})
    t.route("GET", "/api/v3/account", {"accountType": "SPOT", "canTrade": True, "canWithdraw": True, "canDeposit": True,
                                       "balances": [{"asset": "BTC", "free": "0.5", "locked": "0"}]})
    t.route("GET", "/sapi/v1/account/apiRestrictions", {"enableReading": True, "enableSpotAndMarginTrading": True,
                                                        "enableWithdrawals": False, "ipRestrict": False})
    t.route("GET", "/api/v3/exchangeInfo", BTCUSDT_INFO)
    t.route("GET", "/api/v3/ticker/price", {"symbol": "BTCUSDT", "price": "60000"})
    t.route("POST", "/api/v3/order/test", {})
    t.route("POST", "/api/v3/order", {"orderId": 5, "status": "NEW"})
    t.route("GET", "/api/v3/openOrders", [])
    t.route("GET", "/sapi/v1/capital/config/getall", COINS)
    monkeypatch.setattr(appmod, "_urllib_transport", t, raising=False)
    real_client = appmod.BinanceClient
    monkeypatch.setattr(appmod, "BinanceClient", lambda k, s, h, **kw: real_client(k, s, h, transport=t, **kw))
    # run background work synchronously so the test is deterministic
    monkeypatch.setattr(appmod, "_run_in_thread", lambda fn, done, fail, after: _sync(fn, done, fail))
    monkeypatch.setattr(appmod.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(appmod.messagebox, "showerror", lambda *a, **k: None)
    try:
        a = appmod.LunaApp()
    except tk.TclError as e:
        pytest.skip(f"tk unavailable: {e}")
    a.withdraw()
    a._transport = t
    yield a
    a.destroy()


def _sync(fn, done, fail):
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001
        fail(e)
        return
    done(res)


def test_connect_is_read_only_and_wipes_entry_fields(app):
    app.key_var.set(DOC_KEY)
    app.secret_var.set(DOC_SECRET)
    app.connect()
    app.update()
    assert app.client is not None and app.actions.read_only
    assert app.mode_var.get() == "READ-ONLY"
    assert app.key_var.get() == "" and app.secret_var.get() == ""
    assert app.bal_tree.get_children()  # balances filled
    assert str(app.withdraw_chk.cget("state")) == "disabled"  # enableWithdrawals=False from API
    assert str(app.trading_chk.cget("state")) == "normal"
    log = app.log_text.get("1.0", "end")
    assert DOC_KEY not in log and DOC_SECRET not in log


def test_preview_requires_trading_enabled_and_confirmation(app, monkeypatch):
    app.key_var.set(DOC_KEY)
    app.secret_var.set(DOC_SECRET)
    app.connect()
    app.tr_symbol.set("BTCUSDT")
    app.tr_qty.set("0.001")
    app.tr_price.set("50000")
    app.preview_order()
    assert not any(c["path"] == "/api/v3/order" for c in app._transport.calls)

    app.actions.enable_trading(True)
    monkeypatch.setattr(app, "_confirm", lambda *a, **k: False)
    app.preview_order()
    assert any(c["path"] == "/api/v3/order/test" for c in app._transport.calls)
    assert not any(c["path"] == "/api/v3/order" for c in app._transport.calls)

    monkeypatch.setattr(app, "_confirm", lambda *a, **k: True)
    app.preview_order()
    sent = [c for c in app._transport.calls if c["path"] == "/api/v3/order"]
    assert len(sent) == 1 and sent[0]["body"]["price"] == ["50000"]


def test_withdraw_preview_blocked_until_enabled(app, monkeypatch):
    app.key_var.set(DOC_KEY)
    app.secret_var.set(DOC_SECRET)
    app.connect()
    app.load_coins()
    assert "USDT" in app.coins
    app.wd_coin.set("USDT")
    app._fill_networks()
    app.wd_net_cb.set(app.wd_net_cb.cget("values")[0])
    app.wd_address.set("TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf")
    app.wd_amount.set("20")
    monkeypatch.setattr(app, "_confirm", lambda *a, **k: True)
    app.preview_withdrawal()
    assert not any(c["path"].endswith("withdraw/apply") for c in app._transport.calls)
    app.disconnect()
    assert app.client is None and app.mode_var.get() == "READ-ONLY"
