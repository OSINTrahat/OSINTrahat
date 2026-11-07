import pytest

from luna_binance_manager.client import BinanceClient
from luna_binance_manager.errors import BinanceAPIError, NetworkError, RequestTimeout, ServerError
from luna_binance_manager.hosts import HostNotAllowed, check_host, supports_wallet
from luna_binance_manager.redact import Redactor, mask
from luna_binance_manager.signing import encode_params, hmac_sha256_hex, sign_payload

from fakes import DOC_KEY, DOC_SECRET, FakeTransport, make_client

# Official worked example from binance-spot-api-docs rest-api.md ("SIGNED Endpoint Examples").
DOC_QUERY = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
DOC_SIG = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_hmac_matches_binance_doc_example():
    assert hmac_sha256_hex(DOC_SECRET, DOC_QUERY) == DOC_SIG


def test_sign_payload_preserves_order_and_appends_signature():
    params = {"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": 1,
              "price": "0.1", "recvWindow": 5000, "timestamp": 1499827319559}
    assert sign_payload(DOC_SECRET, params) == DOC_QUERY + "&signature=" + DOC_SIG


def test_encode_params_drops_none_and_formats_bools():
    assert encode_params({"a": None, "b": True, "c": "x y"}) == "b=true&c=x%20y"


def test_host_allowlist():
    assert check_host("API.binance.com") == "api.binance.com"
    assert supports_wallet("api1.binance.com")
    assert not supports_wallet("testnet.binance.vision")
    for bad in ("api.binance.com.evil.io", "binance.com", "localhost", "api.binance.us", ""):
        with pytest.raises(HostNotAllowed):
            check_host(bad)
    with pytest.raises(HostNotAllowed):
        BinanceClient(DOC_KEY, DOC_SECRET, "api.binance.com.evil.io", transport=FakeTransport())


def test_signed_get_puts_key_in_header_and_secret_nowhere():
    c, t = make_client()
    t.route("GET", "/api/v3/account", {"balances": [], "canTrade": True})
    c.account()
    call = t.calls[0]
    assert call["headers"]["X-MBX-APIKEY"] == DOC_KEY
    assert call["host"] == "api.binance.com"
    assert call["query"]["timestamp"] == ["1499827319559"]
    assert call["query"]["recvWindow"] == ["5000"]
    assert call["query"]["omitZeroBalances"] == ["true"]
    assert "signature" in call["query"]
    assert DOC_SECRET not in call["raw_url"] and DOC_SECRET not in str(call["headers"])
    assert call["body"] == {}


def test_signed_post_sends_form_body_not_query():
    c, t = make_client()
    t.route("POST", "/api/v3/order", {"orderId": 1, "status": "NEW"})
    c.new_order({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.001"})
    call = t.calls[0]
    assert call["raw_url"] == "https://api.binance.com/api/v3/order"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert call["body"]["symbol"] == ["BTCUSDT"] and "signature" in call["body"]
    assert DOC_SECRET not in call["raw_body"]
    # signature covers the body exactly as sent
    payload, sig = call["raw_body"].rsplit("&signature=", 1)
    assert hmac_sha256_hex(DOC_SECRET, payload) == sig


def test_public_endpoints_do_not_send_api_key():
    c, t = make_client()
    t.route("GET", "/api/v3/time", {"serverTime": 1499827329559})
    assert c.sync_time() == 10000
    assert "X-MBX-APIKEY" not in t.calls[0]["headers"]
    t.route("GET", "/api/v3/account", {"balances": []})
    c.account()
    assert t.calls[1]["query"]["timestamp"] == ["1499827329559"]


def test_api_error_is_raised_and_redacted():
    c, t = make_client()
    t.route("GET", "/api/v3/account", {"code": -2015, "msg": f"Invalid API-key {DOC_KEY}"}, status=401)
    with pytest.raises(BinanceAPIError) as ei:
        c.account()
    assert ei.value.code == -2015 and DOC_KEY not in str(ei.value) and "[REDACTED]" in str(ei.value)


def test_timeout_and_server_errors_map_to_unknown_classes():
    c, t = make_client()
    t.route("POST", "/api/v3/order", TimeoutError("timed out"))
    with pytest.raises(RequestTimeout):
        c.new_order({"symbol": "BTCUSDT"})
    t.route("POST", "/api/v3/order", {"msg": "bad gateway"}, status=502)
    with pytest.raises(ServerError):
        c.new_order({"symbol": "BTCUSDT"})
    t.route("GET", "/api/v3/time", ConnectionError("dns"))
    with pytest.raises(NetworkError):
        c.server_time()
    assert len(t.calls) == 3  # never retried


def test_close_wipes_credentials():
    c, _ = make_client()
    c.close()
    assert c._api_key == "" and c._api_secret == ""


def test_redactor_and_mask():
    r = Redactor()
    r.register(DOC_SECRET, None, "short")
    assert r(f"err {DOC_SECRET} short") == "err [REDACTED] short"
    assert mask("abcdefgh") == "****efgh"
