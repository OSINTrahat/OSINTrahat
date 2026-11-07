"""Withdrawal validation built only from GET /sapi/v1/capital/config/getall.

Every limit (network list, withdrawEnable, withdrawFee, withdrawMin,
withdrawMax, withdrawIntegerMultiple, addressRegex, memoRegex, withdrawTag)
comes from the API response for the connected account; nothing is guessed.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from .errors import ValidationError
from .orders import fmt, is_multiple, parse_decimal


@dataclass(frozen=True)
class NetworkInfo:
    coin: str
    network: str
    name: str
    withdraw_enable: bool
    is_default: bool
    fee: Decimal
    withdraw_min: Decimal
    withdraw_max: Decimal
    integer_multiple: Decimal
    address_regex: str
    memo_regex: str
    withdraw_tag: bool | None  # None when the field is absent from the response
    busy: bool
    withdraw_desc: str
    special_tips: str
    contract_address: str

    @property
    def requires_memo(self) -> bool:
        if self.withdraw_tag is not None:
            return self.withdraw_tag
        return bool(self.memo_regex)

    @property
    def supports_memo(self) -> bool:
        return self.requires_memo


@dataclass(frozen=True)
class CoinInfo:
    coin: str
    name: str
    free: Decimal
    withdraw_all_enable: bool
    networks: tuple[NetworkInfo, ...]

    def network(self, code: str) -> NetworkInfo:
        for n in self.networks:
            if n.network == code:
                return n
        raise ValidationError(f"network {code!r} is not listed for {self.coin}")


def _dec(v: object, default: str = "0") -> Decimal:
    s = str(v).strip() if v is not None else ""
    return Decimal(s) if s else Decimal(default)


def parse_coins(payload: list) -> dict[str, CoinInfo]:
    out: dict[str, CoinInfo] = {}
    for c in payload or []:
        nets = []
        for n in c.get("networkList", []) or []:
            nets.append(NetworkInfo(
                coin=c.get("coin", ""), network=str(n.get("network", "")),
                name=str(n.get("name", "")),
                withdraw_enable=bool(n.get("withdrawEnable", False)),
                is_default=bool(n.get("isDefault", False)),
                fee=_dec(n.get("withdrawFee")), withdraw_min=_dec(n.get("withdrawMin")),
                withdraw_max=_dec(n.get("withdrawMax")),
                integer_multiple=_dec(n.get("withdrawIntegerMultiple")),
                address_regex=str(n.get("addressRegex") or ""),
                memo_regex=str(n.get("memoRegex") or ""),
                withdraw_tag=(None if "withdrawTag" not in n else bool(n.get("withdrawTag"))),
                busy=bool(n.get("busy", False)),
                withdraw_desc=str(n.get("withdrawDesc") or ""),
                special_tips=str(n.get("specialTips") or n.get("specialWithdrawTips") or ""),
                contract_address=str(n.get("contractAddress") or ""),
            ))
        out[c["coin"]] = CoinInfo(
            coin=c["coin"], name=str(c.get("name", "")), free=_dec(c.get("free")),
            withdraw_all_enable=bool(c.get("withdrawAllEnable", False)), networks=tuple(nets),
        )
    return out


@dataclass(frozen=True)
class PreparedWithdrawal:
    coin: str
    network: str
    network_name: str
    address: str
    memo: str | None
    amount: Decimal
    fee: Decimal
    withdraw_order_id: str

    @property
    def expected_received(self) -> Decimal:
        # Binance deducts the network fee from the requested amount.
        return self.amount - self.fee

    def to_params(self) -> dict:
        p = {"coin": self.coin, "network": self.network, "address": self.address,
             "amount": fmt(self.amount), "withdrawOrderId": self.withdraw_order_id}
        if self.memo:
            p["addressTag"] = self.memo
        return p


def new_withdraw_order_id() -> str:
    return "luna" + uuid.uuid4().hex[:28]


def _regex_ok(pattern: str, value: str) -> bool:
    if not pattern:
        return True
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        # Unparseable regex from API: do not block, Binance validates server-side.
        return True


def prepare_withdrawal(coin: CoinInfo, network_code: str, address: str, memo: str,
                       amount: str, *, withdraw_order_id: str | None = None) -> PreparedWithdrawal:
    net = coin.network(network_code)
    if not coin.withdraw_all_enable:
        raise ValidationError(f"withdrawals are currently disabled for {coin.coin} (withdrawAllEnable=false)")
    if not net.withdraw_enable:
        raise ValidationError(f"withdrawals on {net.network} are currently disabled (withdrawEnable=false)")
    if net.busy:
        raise ValidationError(f"network {net.network} is reported busy by Binance; try later")

    addr = (address or "").strip()
    if not addr:
        raise ValidationError("destination address is required")
    if any(ch.isspace() for ch in addr):
        raise ValidationError("address must not contain whitespace")
    if not _regex_ok(net.address_regex, addr):
        raise ValidationError(f"address does not match the {net.network} address format published by Binance")

    memo = (memo or "").strip() or None
    if net.requires_memo:
        if not memo:
            raise ValidationError(f"{net.network} requires a memo/tag for {coin.coin}")
        if not _regex_ok(net.memo_regex, memo):
            raise ValidationError("memo/tag does not match the format published by Binance")
    elif memo:
        raise ValidationError(f"{net.network} does not support a memo/tag; leave it empty "
                              "(Binance returns -4106 otherwise)")

    amt = parse_decimal(amount, "amount")
    if amt <= 0:
        raise ValidationError("amount must be positive")
    if net.withdraw_min > 0 and amt < net.withdraw_min:
        raise ValidationError(f"amount {fmt(amt)} is below withdrawMin {fmt(net.withdraw_min)}")
    if net.withdraw_max > 0 and amt > net.withdraw_max:
        raise ValidationError(f"amount {fmt(amt)} is above withdrawMax {fmt(net.withdraw_max)}")
    if net.integer_multiple > 0 and not is_multiple(amt, net.integer_multiple):
        raise ValidationError(f"amount must be a multiple of {fmt(net.integer_multiple)} (withdrawIntegerMultiple)")
    if amt <= net.fee:
        raise ValidationError(f"amount {fmt(amt)} does not exceed the network fee {fmt(net.fee)}")
    if coin.free > 0 and amt > coin.free:
        raise ValidationError(f"amount {fmt(amt)} exceeds free balance {fmt(coin.free)} {coin.coin}")

    return PreparedWithdrawal(
        coin=coin.coin, network=net.network, network_name=net.name, address=addr, memo=memo,
        amount=amt, fee=net.fee, withdraw_order_id=withdraw_order_id or new_withdraw_order_id(),
    )
