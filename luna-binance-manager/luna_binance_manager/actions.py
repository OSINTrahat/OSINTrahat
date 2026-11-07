"""Action gate: read-only by default, single in-flight action, no auto-retry.

Rules enforced here (independent of the GUI):
- trading / withdrawals stay disabled until explicitly enabled
- at most one action in flight (double-click protection)
- a timeout, HTTP 5XX or -1007 marks the action UNKNOWN; the same kind of
  action is blocked until `reconcile()` has queried Binance for its outcome
- nothing is ever retried automatically
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .client import BinanceClient
from .errors import ActionBlocked, BinanceAPIError, RequestTimeout, ServerError, UnknownStatus
from .orders import PreparedOrder
from .withdrawals import PreparedWithdrawal

KIND_ORDER = "order"
KIND_CANCEL = "cancel"
KIND_WITHDRAW = "withdraw"

# Withdraw history status codes from the official docs.
WITHDRAW_STATUS = {0: "Email Sent", 1: "Cancelled", 2: "Awaiting Approval", 3: "Rejected",
                   4: "Processing", 5: "Failure", 6: "Completed"}


@dataclass
class UnknownAction:
    kind: str
    description: str
    symbol: str | None = None
    client_order_id: str | None = None
    order_id: int | None = None
    withdraw_order_id: str | None = None
    created: float = field(default_factory=time.time)
    reason: str = ""
    resolution: str | None = None  # set by reconcile()


class ActionController:
    def __init__(self, client: BinanceClient, log: Callable[[str], None] | None = None) -> None:
        self.client = client
        self.trading_enabled = False
        self.withdrawals_enabled = False
        self._lock = threading.Lock()
        self._in_flight: str | None = None
        self.unknown: list[UnknownAction] = []
        self._log = log or (lambda s: None)

    # -- state ----------------------------------------------------------
    @property
    def read_only(self) -> bool:
        return not (self.trading_enabled or self.withdrawals_enabled)

    @property
    def in_flight(self) -> str | None:
        return self._in_flight

    def unresolved(self, kind: str | None = None) -> list[UnknownAction]:
        return [u for u in self.unknown if u.resolution is None and (kind is None or u.kind == kind)]

    def enable_trading(self, enabled: bool) -> None:
        self.trading_enabled = bool(enabled)
        self._log(f"trading {'ENABLED' if enabled else 'disabled'}")

    def enable_withdrawals(self, enabled: bool) -> None:
        self.withdrawals_enabled = bool(enabled)
        self._log(f"withdrawals {'ENABLED' if enabled else 'disabled'}")

    # -- gate -----------------------------------------------------------
    def _begin(self, kind: str) -> None:
        with self._lock:
            if self._in_flight is not None:
                raise ActionBlocked(f"another action ({self._in_flight}) is still in progress")
            blocked = self.unresolved()
            if blocked:
                raise ActionBlocked(
                    f"{len(blocked)} previous action(s) have UNKNOWN status; reconcile them first")
            self._in_flight = kind

    def _end(self) -> None:
        with self._lock:
            self._in_flight = None

    def _run(self, kind: str, confirmed: bool, enabled: bool, feature: str,
             fn: Callable[[], Any], unknown: UnknownAction) -> Any:
        if not confirmed:
            raise ActionBlocked("explicit confirmation is required")
        if not enabled:
            raise ActionBlocked(f"{feature} is disabled (read-only mode)")
        self._begin(kind)
        try:
            return fn()
        except (RequestTimeout, ServerError) as e:
            unknown.reason = str(e)
            self.unknown.append(unknown)
            self._log(f"UNKNOWN status: {unknown.description}: {e}")
            raise UnknownStatus(f"{unknown.description}: {e}. Status UNKNOWN; reconcile before retrying.") from None
        except BinanceAPIError as e:
            if e.is_unknown_status:
                unknown.reason = str(e)
                self.unknown.append(unknown)
                self._log(f"UNKNOWN status: {unknown.description}: {e}")
                raise UnknownStatus(f"{unknown.description}: {e}. Status UNKNOWN; reconcile before retrying.") from None
            raise
        finally:
            self._end()

    # -- actions --------------------------------------------------------
    def place_order(self, order: PreparedOrder, *, confirmed: bool) -> dict:
        desc = f"{order.side} {order.type} {order.symbol} clientOrderId={order.client_order_id}"
        u = UnknownAction(KIND_ORDER, desc, symbol=order.symbol, client_order_id=order.client_order_id)
        res = self._run(KIND_ORDER, confirmed, self.trading_enabled, "trading",
                        lambda: self.client.new_order(order.to_params()), u)
        self._log(f"order placed: {desc} orderId={res.get('orderId')} status={res.get('status')}")
        return res

    def cancel_order(self, symbol: str, order_id: int, *, confirmed: bool) -> dict:
        desc = f"CANCEL {symbol} orderId={order_id}"
        u = UnknownAction(KIND_CANCEL, desc, symbol=symbol, order_id=order_id)
        res = self._run(KIND_CANCEL, confirmed, self.trading_enabled, "trading",
                        lambda: self.client.cancel_order(symbol, order_id), u)
        self._log(f"cancel sent: {desc} status={res.get('status')}")
        return res

    def withdraw(self, w: PreparedWithdrawal, *, confirmed: bool) -> dict:
        desc = f"WITHDRAW {w.amount} {w.coin} via {w.network} withdrawOrderId={w.withdraw_order_id}"
        u = UnknownAction(KIND_WITHDRAW, desc, withdraw_order_id=w.withdraw_order_id)
        res = self._run(KIND_WITHDRAW, confirmed, self.withdrawals_enabled, "withdrawals",
                        lambda: self.client.withdraw_apply(w.to_params()), u)
        self._log(f"withdrawal submitted: {desc} id={res.get('id')}")
        return res

    # -- reconciliation -------------------------------------------------
    def reconcile(self) -> list[UnknownAction]:
        """Query Binance for every unresolved action. Never resubmits anything."""
        done = []
        for u in self.unresolved():
            try:
                if u.kind == KIND_ORDER:
                    o = self.client.get_order(u.symbol, orig_client_order_id=u.client_order_id)
                    u.resolution = f"order exists: orderId={o.get('orderId')} status={o.get('status')}"
                elif u.kind == KIND_CANCEL:
                    o = self.client.get_order(u.symbol, order_id=u.order_id)
                    u.resolution = f"order status now {o.get('status')}"
                else:
                    hist = self.client.withdraw_history(withdraw_order_id=u.withdraw_order_id)
                    if hist:
                        h = hist[0]
                        st = WITHDRAW_STATUS.get(h.get("status"), str(h.get("status")))
                        u.resolution = f"withdrawal exists: id={h.get('id')} status={st}"
                    else:
                        u.resolution = "not found on Binance: withdrawal was NOT submitted"
            except BinanceAPIError as e:
                if u.kind in (KIND_ORDER, KIND_CANCEL) and e.code in (-2013,):  # Order does not exist
                    u.resolution = "not found on Binance: order was NOT placed"
                else:
                    self._log(f"reconcile failed for {u.description}: {e}")
                    continue
            except Exception as e:  # network problems: keep it unresolved
                self._log(f"reconcile failed for {u.description}: {self.client.redact(e)}")
                continue
            self._log(f"reconciled: {u.description} -> {u.resolution}")
            done.append(u)
        return done
