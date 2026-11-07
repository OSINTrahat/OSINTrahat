from decimal import Decimal

import pytest

from luna_binance_manager.errors import ValidationError
from luna_binance_manager.withdrawals import parse_coins, prepare_withdrawal

from fakes import COINS, TRX_ADDR, XRP_ADDR

C = parse_coins(COINS)


def test_parse_networks_from_api():
    usdt = C["USDT"]
    trx = usdt.network("TRX")
    assert trx.fee == Decimal("1") and trx.withdraw_min == Decimal("10") and trx.withdraw_enable
    assert not trx.requires_memo and C["XRP"].network("XRP").requires_memo
    assert usdt.free == Decimal("100.5")


def test_valid_withdrawal_preview_and_params():
    w = prepare_withdrawal(C["USDT"], "TRX", f" {TRX_ADDR} ", "", "25.5", withdraw_order_id="wid1")
    assert w.expected_received == Decimal("24.5")
    assert w.to_params() == {"coin": "USDT", "network": "TRX", "address": TRX_ADDR, "amount": "25.5", "withdrawOrderId": "wid1"}
    x = prepare_withdrawal(C["XRP"], "XRP", XRP_ADDR, "12345", "5")
    assert x.to_params()["addressTag"] == "12345" and x.withdraw_order_id.startswith("luna")


@pytest.mark.parametrize("coin,net,addr,memo,amt,msg", [
    ("USDT", "ETH", "0x" + "a" * 40, "", "20", "withdrawEnable=false"),
    ("USDT", "BSC", TRX_ADDR, "", "20", "not listed"),
    ("USDT", "TRX", "", "", "20", "address is required"),
    ("USDT", "TRX", "0x" + "a" * 40, "", "20", "address format"),
    ("USDT", "TRX", TRX_ADDR, "memo", "20", "does not support a memo"),
    ("USDT", "TRX", TRX_ADDR, "", "5", "below withdrawMin"),
    ("USDT", "TRX", TRX_ADDR, "", "20.0000001", "multiple of 0.000001"),
    ("USDT", "TRX", TRX_ADDR, "", "500", "exceeds free balance"),
    ("USDT", "TRX", TRX_ADDR, "", "0", "positive"),
    ("USDT", "TRX", TRX_ADDR, "", "x", "not a valid number"),
    ("XRP", "XRP", XRP_ADDR, "", "5", "requires a memo"),
    ("XRP", "XRP", XRP_ADDR, "0abc", "5", "memo/tag does not match"),
    ("USDT", "TRX", TRX_ADDR, "", "0.5", "below withdrawMin"),
])
def test_withdrawal_rejections(coin, net, addr, memo, amt, msg):
    with pytest.raises(ValidationError, match=msg):
        prepare_withdrawal(C[coin], net, addr, memo, amt)


def test_coin_level_disable_and_busy():
    coins = parse_coins([dict(COINS[0], withdrawAllEnable=False)])
    with pytest.raises(ValidationError, match="withdrawAllEnable=false"):
        prepare_withdrawal(coins["USDT"], "TRX", TRX_ADDR, "", "20")
    busy = [dict(COINS[0], networkList=[dict(COINS[0]["networkList"][0], busy=True)])]
    with pytest.raises(ValidationError, match="busy"):
        prepare_withdrawal(parse_coins(busy)["USDT"], "TRX", TRX_ADDR, "", "20")


def test_memo_requirement_falls_back_to_memo_regex_when_withdraw_tag_absent():
    nl = dict(COINS[1]["networkList"][0])
    del nl["withdrawTag"]
    coins = parse_coins([dict(COINS[1], networkList=[nl])])
    assert coins["XRP"].network("XRP").requires_memo


def test_amount_must_exceed_fee():
    nl = dict(COINS[0]["networkList"][0], withdrawMin="0.5", withdrawFee="1")
    coins = parse_coins([dict(COINS[0], networkList=[nl])])
    with pytest.raises(ValidationError, match="does not exceed the network fee"):
        prepare_withdrawal(coins["USDT"], "TRX", TRX_ADDR, "", "0.8")
