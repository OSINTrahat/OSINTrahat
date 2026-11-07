"""Tkinter desktop GUI for Luna Binance Manager."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import __version__
from .actions import WITHDRAW_STATUS, ActionController
from .client import BinanceClient
from .errors import LunaError
from .hosts import DEFAULT_LIVE_HOST, DEFAULT_TESTNET_HOST, supports_wallet
from .orders import SymbolRules, fmt, parse_decimal, parse_symbol, prepare_order
from .redact import Redactor, mask
from .withdrawals import CoinInfo, parse_coins, prepare_withdrawal

PERMISSION_HELP = (
    "Binance API key setup (done on binance.com, never by this app):\n"
    "  • Read-only use: 'Enable Reading' is enough.\n"
    "  • Spot trading: 'Enable Spot & Margin Trading'.\n"
    "  • Withdrawals: 'Enable Withdrawals' is only offered when the key is restricted to\n"
    "    trusted IPs ('Restrict access to trusted IPs only'). Add your fixed public IP.\n"
    "  • Withdrawals via API are only allowed to addresses on your Binance Withdrawal\n"
    "    Address Whitelist (Address Management). Enable whitelist and add each address first.\n"
    "  • This app never changes any of these settings and never stores your key/secret."
)


def _run_in_thread(fn: Callable[[], Any], done: Callable[[Any], None], fail: Callable[[Exception], None],
                   after: Callable[..., Any]) -> None:
    def worker() -> None:
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 - surfaced to UI
            after(0, fail, e)
            return
        after(0, done, res)
    threading.Thread(target=worker, daemon=True).start()


class ConfirmDialog(tk.Toplevel):
    """Modal preview + explicit confirmation. Optionally requires typing a word."""

    def __init__(self, parent: tk.Misc, title: str, lines: list[str], *,
                 require_text: str | None = None, ok_label: str = "Confirm") -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = False
        self.transient(parent)
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        txt = tk.Text(frm, width=72, height=min(20, len(lines) + 2), wrap="word")
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        self._entry: ttk.Entry | None = None
        if require_text:
            ttk.Label(frm, text=f"Type {require_text} to confirm:").pack(anchor="w", pady=(8, 0))
            self._entry = ttk.Entry(frm)
            self._entry.pack(fill="x")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        self._ok = ttk.Button(btns, text=ok_label, command=self._confirm)
        self._ok.pack(side="right", padx=(0, 8))
        self._require = require_text
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        if self._entry is not None:
            self._entry.focus_set()

    def _confirm(self) -> None:
        if self._require and (self._entry is None or self._entry.get().strip() != self._require):
            messagebox.showwarning("Not confirmed", f"You must type {self._require} exactly.", parent=self)
            return
        self.result = True
        self.destroy()


class LunaApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Luna Binance Manager v{__version__}")
        self.geometry("980x640")
        self.redactor = Redactor()
        self.client: BinanceClient | None = None
        self.actions: ActionController | None = None
        self.restrictions: dict = {}
        self.coins: dict[str, CoinInfo] = {}
        self._busy = False
        self._symbol_cache: dict[str, SymbolRules] = {}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        self.status_var = tk.StringVar(value="Disconnected")
        self.mode_var = tk.StringVar(value="READ-ONLY")
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        self.mode_label = ttk.Label(bar, textvariable=self.mode_var, foreground="green",
                                    font=("TkDefaultFont", 10, "bold"))
        self.mode_label.pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self._build_connect()
        self._build_balances()
        self._build_open_orders()
        self._build_trades()
        self._build_trade()
        self._build_withdraw()
        self._build_log()
        self._set_connected(False)

    def _build_connect(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Connect")
        ttk.Label(f, text="API key").grid(row=0, column=0, sticky="w")
        self.key_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.key_var, show="•", width=70).grid(row=0, column=1, sticky="ew")
        ttk.Label(f, text="API secret").grid(row=1, column=0, sticky="w")
        self.secret_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.secret_var, show="•", width=70).grid(row=1, column=1, sticky="ew")
        self.env_var = tk.StringVar(value="live")
        env = ttk.Frame(f)
        env.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Radiobutton(env, text=f"Live ({DEFAULT_LIVE_HOST})", variable=self.env_var, value="live").pack(side="left")
        ttk.Radiobutton(env, text=f"Spot testnet ({DEFAULT_TESTNET_HOST}, no wallet)", variable=self.env_var,
                        value="testnet").pack(side="left", padx=10)
        btns = ttk.Frame(f)
        btns.grid(row=3, column=1, sticky="w", pady=4)
        self.connect_btn = ttk.Button(btns, text="Connect (read-only)", command=self.connect)
        self.connect_btn.pack(side="left")
        self.disconnect_btn = ttk.Button(btns, text="Disconnect & wipe", command=self.disconnect)
        self.disconnect_btn.pack(side="left", padx=8)

        self.perm_var = tk.StringVar(value="Not connected.")
        ttk.Label(f, textvariable=self.perm_var, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=6)

        safety = ttk.LabelFrame(f, text="Safety switches (default: read-only)", padding=8)
        safety.grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)
        self.trading_var = tk.BooleanVar(value=False)
        self.withdraw_var = tk.BooleanVar(value=False)
        self.trading_chk = ttk.Checkbutton(safety, text="Enable manual spot trading (buy/sell/cancel)",
                                           variable=self.trading_var, command=self._toggle_trading)
        self.trading_chk.pack(anchor="w")
        self.withdraw_chk = ttk.Checkbutton(safety, text="Enable manual withdrawals",
                                            variable=self.withdraw_var, command=self._toggle_withdraw)
        self.withdraw_chk.pack(anchor="w")
        ttk.Label(f, text=PERMISSION_HELP, justify="left").grid(row=6, column=0, columnspan=2, sticky="w")
        f.columnconfigure(1, weight=1)

    def _tree(self, parent: tk.Misc, cols: tuple[str, ...], widths: tuple[int, ...] | None = None) -> ttk.Treeview:
        tv = ttk.Treeview(parent, columns=cols, show="headings", height=18)
        for i, c in enumerate(cols):
            tv.heading(c, text=c)
            tv.column(c, width=(widths[i] if widths else 120), anchor="w")
        tv.pack(fill="both", expand=True)
        return tv

    def _build_balances(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Balances")
        ttk.Button(f, text="Refresh", command=self.refresh_balances).pack(anchor="w")
        self.bal_tree = self._tree(f, ("Asset", "Free", "Locked"))

    def _build_open_orders(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Open orders")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Symbol (optional)").pack(side="left")
        self.oo_symbol = tk.StringVar()
        ttk.Entry(top, textvariable=self.oo_symbol, width=14).pack(side="left", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh_open_orders).pack(side="left")
        self.cancel_btn = ttk.Button(top, text="Cancel selected…", command=self.cancel_selected)
        self.cancel_btn.pack(side="left", padx=8)
        self.oo_tree = self._tree(f, ("OrderId", "Symbol", "Side", "Type", "Price", "Qty", "Filled", "Status", "ClientId"),
                                  (90, 90, 50, 70, 110, 110, 110, 90, 200))

    def _build_trades(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Trade history")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Symbol (required)").pack(side="left")
        self.th_symbol = tk.StringVar()
        ttk.Entry(top, textvariable=self.th_symbol, width=14).pack(side="left", padx=4)
        ttk.Button(top, text="Refresh", command=self.refresh_trades).pack(side="left")
        self.th_tree = self._tree(f, ("Time", "Symbol", "Side", "Price", "Qty", "Quote", "Fee", "OrderId"),
                                  (150, 90, 50, 110, 110, 110, 120, 100))

    def _build_trade(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Trade")
        g = ttk.Frame(f)
        g.pack(anchor="w")
        self.tr_symbol = tk.StringVar()
        self.tr_side = tk.StringVar(value="BUY")
        self.tr_type = tk.StringVar(value="LIMIT")
        self.tr_qty = tk.StringVar()
        self.tr_quote = tk.StringVar()
        self.tr_price = tk.StringVar()
        rows = [("Symbol", ttk.Entry(g, textvariable=self.tr_symbol, width=16)),
                ("Side", ttk.Combobox(g, textvariable=self.tr_side, values=("BUY", "SELL"), state="readonly", width=14)),
                ("Type", ttk.Combobox(g, textvariable=self.tr_type, values=("LIMIT", "MARKET"), state="readonly", width=14)),
                ("Quantity (base)", ttk.Entry(g, textvariable=self.tr_qty, width=16)),
                ("Quote amount (MARKET only)", ttk.Entry(g, textvariable=self.tr_quote, width=16)),
                ("Price (LIMIT only)", ttk.Entry(g, textvariable=self.tr_price, width=16))]
        for i, (lbl, w) in enumerate(rows):
            ttk.Label(g, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            w.grid(row=i, column=1, sticky="w", padx=6)
        self.order_btn = ttk.Button(f, text="Preview order…", command=self.preview_order)
        self.order_btn.pack(anchor="w", pady=8)
        ttk.Label(f, text="Orders are validated with Decimal against the symbol's Binance filters, then "
                          "checked with POST /api/v3/order/test, then shown for confirmation. "
                          "Nothing is sent without your confirmation.", wraplength=800, justify="left").pack(anchor="w")

    def _build_withdraw(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Withdraw")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.coins_btn = ttk.Button(top, text="Load coins & networks from Binance", command=self.load_coins)
        self.coins_btn.pack(side="left")
        ttk.Button(top, text="Refresh withdrawal history", command=self.refresh_withdraw_history).pack(side="left", padx=8)
        g = ttk.Frame(f)
        g.pack(anchor="w", pady=6)
        self.wd_coin = tk.StringVar()
        self.wd_network = tk.StringVar()
        self.wd_address = tk.StringVar()
        self.wd_memo = tk.StringVar()
        self.wd_amount = tk.StringVar()
        self.wd_coin_cb = ttk.Combobox(g, textvariable=self.wd_coin, state="readonly", width=14)
        self.wd_coin_cb.bind("<<ComboboxSelected>>", lambda _e: self._fill_networks())
        self.wd_net_cb = ttk.Combobox(g, textvariable=self.wd_network, state="readonly", width=60)
        self.wd_net_cb.bind("<<ComboboxSelected>>", lambda _e: self._show_network_info())
        rows = [("Coin", self.wd_coin_cb), ("Network", self.wd_net_cb),
                ("Destination address", ttk.Entry(g, textvariable=self.wd_address, width=64)),
                ("Memo / tag (only if required)", ttk.Entry(g, textvariable=self.wd_memo, width=40)),
                ("Amount", ttk.Entry(g, textvariable=self.wd_amount, width=20))]
        for i, (lbl, w) in enumerate(rows):
            ttk.Label(g, text=lbl).grid(row=i, column=0, sticky="w", pady=2)
            w.grid(row=i, column=1, sticky="w", padx=6)
        self.wd_info = tk.StringVar(value="Load coins to see fee / minimum / memo requirements from Binance.")
        ttk.Label(f, textvariable=self.wd_info, justify="left", wraplength=900).pack(anchor="w")
        self.withdraw_btn = ttk.Button(f, text="Preview withdrawal…", command=self.preview_withdrawal)
        self.withdraw_btn.pack(anchor="w", pady=8)
        self.wd_tree = self._tree(f, ("Applied", "Coin", "Network", "Amount", "Fee", "Status", "Address", "TxId"),
                                  (140, 60, 70, 110, 90, 110, 220, 160))
        self.wd_tree.configure(height=8)

    def _build_log(self) -> None:
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Log / Unknown actions")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.reconcile_btn = ttk.Button(top, text="Reconcile unknown actions with Binance", command=self.reconcile)
        self.reconcile_btn.pack(side="left")
        self.unknown_var = tk.StringVar(value="No unknown actions.")
        ttk.Label(top, textvariable=self.unknown_var, foreground="red").pack(side="left", padx=10)
        self.log_text = tk.Text(f, height=20, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {self.redactor(msg)}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _error(self, e: Exception, title: str = "Error") -> None:
        msg = self.redactor(e)
        self.log(f"{title}: {msg}")
        messagebox.showerror(title, msg, parent=self)
        self._set_busy(False)
        self._refresh_unknown()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.order_btn, self.cancel_btn, self.withdraw_btn, self.connect_btn, self.reconcile_btn):
            b.configure(state=state)
        self.update_idletasks()

    def _bg(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self._busy:
            return
        self._set_busy(True)

        def ok(res: Any) -> None:
            self._set_busy(False)
            self._refresh_unknown()
            done(res)
        _run_in_thread(fn, ok, self._error, self.after)

    def _require_client(self) -> BinanceClient:
        if self.client is None:
            raise LunaError("not connected")
        return self.client

    def _set_connected(self, on: bool) -> None:
        self.disconnect_btn.configure(state="normal" if on else "disabled")
        for chk in (self.trading_chk, self.withdraw_chk):
            chk.configure(state="normal" if on else "disabled")
        if not on:
            self.trading_var.set(False)
            self.withdraw_var.set(False)
            self._update_mode()

    def _update_mode(self) -> None:
        if self.actions is None or self.actions.read_only:
            self.mode_var.set("READ-ONLY")
            self.mode_label.configure(foreground="green")
        else:
            parts = []
            if self.actions.trading_enabled:
                parts.append("TRADING ENABLED")
            if self.actions.withdrawals_enabled:
                parts.append("WITHDRAWALS ENABLED")
            self.mode_var.set(" + ".join(parts))
            self.mode_label.configure(foreground="red")

    def _refresh_unknown(self) -> None:
        if self.actions is None:
            self.unknown_var.set("No unknown actions.")
            return
        u = self.actions.unresolved()
        self.unknown_var.set("No unknown actions." if not u else
                             f"{len(u)} action(s) with UNKNOWN status - all new actions blocked until reconciled.")

    def _confirm(self, title: str, lines: list[str], require_text: str | None = None, ok_label: str = "Confirm") -> bool:
        d = ConfirmDialog(self, title, lines, require_text=require_text, ok_label=ok_label)
        self.wait_window(d)
        return d.result

    # ------------------------------------------------------------ connect
    def connect(self) -> None:
        key, secret = self.key_var.get().strip(), self.secret_var.get().strip()
        if not key or not secret:
            messagebox.showwarning("Missing", "Enter both API key and secret.", parent=self)
            return
        host = DEFAULT_LIVE_HOST if self.env_var.get() == "live" else DEFAULT_TESTNET_HOST
        try:
            client = BinanceClient(key, secret, host, redactor=self.redactor)
        except Exception as e:  # noqa: BLE001
            self._error(e, "Connect")
            return
        self.key_var.set("")
        self.secret_var.set("")

        def work() -> dict:
            client.sync_time()
            acct = client.account()
            restr = client.api_restrictions() if supports_wallet(host) else {}
            return {"acct": acct, "restr": restr}

        def done(res: dict) -> None:
            self.client = client
            self.actions = ActionController(client, self.log)
            self.restrictions = res["restr"]
            self.status_var.set(f"Connected to {host} - key …{mask(key)[-4:]} - read-only")
            self._set_connected(True)
            self._show_permissions(res["acct"], res["restr"])
            self._fill_balances(res["acct"])
            self._update_mode()
            self.log(f"connected to {host} (read-only); account type {res['acct'].get('accountType')}")
        self._bg(work, done)

    def _show_permissions(self, acct: dict, r: dict) -> None:
        if r:
            lines = [
                f"API key permissions reported by Binance: reading={r.get('enableReading')}  "
                f"spot trading={r.get('enableSpotAndMarginTrading')}  withdrawals={r.get('enableWithdrawals')}  "
                f"IP restricted={r.get('ipRestrict')}",
            ]
            if not r.get("ipRestrict"):
                lines.append("Key is not IP-restricted: Binance will not allow withdrawals on this key.")
            self.withdraw_chk.configure(state="normal" if r.get("enableWithdrawals") else "disabled")
        else:
            lines = ["Spot testnet: wallet endpoints are unavailable; withdrawals disabled."]
            self.withdraw_chk.configure(state="disabled")
        self.trading_chk.configure(state="normal" if (acct.get("canTrade") and (not r or r.get("enableSpotAndMarginTrading"))) else "disabled")
        lines.append(f"Account: canTrade={acct.get('canTrade')} canWithdraw={acct.get('canWithdraw')} canDeposit={acct.get('canDeposit')}")
        self.perm_var.set("\n".join(lines))

    def disconnect(self) -> None:
        if self.client:
            self.client.close()
        self.client = None
        self.actions = None
        self.coins = {}
        self.redactor.clear()
        self.status_var.set("Disconnected")
        self.perm_var.set("Not connected.")
        self._set_connected(False)
        for tv in (self.bal_tree, self.oo_tree, self.th_tree, self.wd_tree):
            tv.delete(*tv.get_children())
        self.log("disconnected; credentials wiped from memory")

    def _on_close(self) -> None:
        self.disconnect()
        self.destroy()

    def _toggle_trading(self) -> None:
        if self.actions is None:
            self.trading_var.set(False)
            return
        if self.trading_var.get():
            ok = self._confirm("Enable trading?", [
                "You are about to enable MANUAL spot trading in this app.",
                "Every buy, sell or cancel will still show a preview and ask for confirmation.",
                "There is no automated trading. You can disable this at any time.",
            ], ok_label="Enable trading")
            if not ok:
                self.trading_var.set(False)
                return
        self.actions.enable_trading(self.trading_var.get())
        self._update_mode()

    def _toggle_withdraw(self) -> None:
        if self.actions is None:
            self.withdraw_var.set(False)
            return
        if self.withdraw_var.get():
            ok = self._confirm("Enable withdrawals?", [
                "You are about to enable MANUAL crypto withdrawals in this app.",
                "Each withdrawal shows coin, network, address, memo, amount, fee and expected received",
                "amount, then requires you to type WITHDRAW. Nothing is scheduled or automatic.",
                "Binance only allows API withdrawals to whitelisted addresses from a trusted IP.",
            ], require_text="ENABLE", ok_label="Enable withdrawals")
            if not ok:
                self.withdraw_var.set(False)
                return
        self.actions.enable_withdrawals(self.withdraw_var.get())
        self._update_mode()

    # ------------------------------------------------------------ read-only views
    def _fill_balances(self, acct: dict) -> None:
        self.bal_tree.delete(*self.bal_tree.get_children())
        for b in sorted(acct.get("balances", []), key=lambda x: x["asset"]):
            self.bal_tree.insert("", "end", values=(b["asset"], b["free"], b["locked"]))

    def refresh_balances(self) -> None:
        c = self._require_client()
        self._bg(c.account, self._fill_balances)

    def refresh_open_orders(self) -> None:
        c = self._require_client()
        sym = self.oo_symbol.get().strip().upper() or None
        if sym:
            sym = parse_symbol(sym)

        def done(orders: list) -> None:
            self.oo_tree.delete(*self.oo_tree.get_children())
            for o in orders:
                self.oo_tree.insert("", "end", values=(o["orderId"], o["symbol"], o["side"], o["type"], o["price"],
                                                       o["origQty"], o["executedQty"], o["status"], o.get("clientOrderId", "")))
        self._bg(lambda: c.open_orders(sym), done)

    def refresh_trades(self) -> None:
        c = self._require_client()
        try:
            sym = parse_symbol(self.th_symbol.get())
        except LunaError as e:
            self._error(e, "Trade history")
            return

        def done(trades: list) -> None:
            self.th_tree.delete(*self.th_tree.get_children())
            for t in sorted(trades, key=lambda x: x["time"], reverse=True):
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["time"] / 1000))
                self.th_tree.insert("", "end", values=(ts, t["symbol"], "BUY" if t["isBuyer"] else "SELL", t["price"],
                                                       t["qty"], t["quoteQty"], f"{t['commission']} {t['commissionAsset']}",
                                                       t["orderId"]))
        self._bg(lambda: c.my_trades(sym), done)

    # ------------------------------------------------------------ trading
    def _rules(self, c: BinanceClient, symbol: str) -> SymbolRules:
        if symbol not in self._symbol_cache:
            self._symbol_cache[symbol] = SymbolRules.from_exchange_info(c.exchange_info(symbol), symbol)
        return self._symbol_cache[symbol]

    def preview_order(self) -> None:
        c = self._require_client()
        if self.actions is None or not self.actions.trading_enabled:
            messagebox.showinfo("Read-only", "Enable trading on the Connect tab first.", parent=self)
            return
        try:
            symbol = parse_symbol(self.tr_symbol.get())
        except LunaError as e:
            self._error(e, "Order")
            return
        side, otype = self.tr_side.get(), self.tr_type.get()
        qty, quote, price = self.tr_qty.get().strip(), self.tr_quote.get().strip(), self.tr_price.get().strip()

        def work() -> dict:
            rules = self._rules(c, symbol)
            ref = parse_decimal(c.ticker_price(symbol)["price"], "ticker price")
            order = prepare_order(rules, side, otype, quantity=qty or None, quote_qty=quote or None,
                                  price=price or None, reference_price=ref)
            c.test_order(order.to_params())
            return {"order": order, "rules": rules}

        def done(res: dict) -> None:
            o, rules = res["order"], res["rules"]
            lines = [f"Symbol: {o.symbol}   Side: {o.side}   Type: {o.type}"]
            if o.quantity is not None:
                lines.append(f"Quantity: {fmt(o.quantity)} {rules.base}")
            if o.quote_qty is not None:
                lines.append(f"Quote amount: {fmt(o.quote_qty)} {rules.quote}")
            if o.price is not None:
                lines.append(f"Limit price: {fmt(o.price)} {rules.quote}")
            lines.append(f"Current price: {fmt(o.reference_price)} {rules.quote}")
            if o.estimated_notional is not None:
                lines.append(f"Estimated value: {fmt(o.estimated_notional)} {rules.quote}")
            lines.append(f"Client order id: {o.client_order_id}")
            lines.append("Binance /order/test accepted these parameters. Submit this order now?")
            if not self._confirm("Confirm order", lines, ok_label=f"Place {o.side} order"):
                self.log("order preview cancelled by user")
                return
            self._bg(lambda: self.actions.place_order(o, confirmed=True),
                     lambda r: (messagebox.showinfo("Order placed", f"orderId {r.get('orderId')} status {r.get('status')}", parent=self),
                                self.refresh_open_orders()))
        self._bg(work, done)

    def cancel_selected(self) -> None:
        c = self._require_client()
        if self.actions is None or not self.actions.trading_enabled:
            messagebox.showinfo("Read-only", "Enable trading on the Connect tab first.", parent=self)
            return
        sel = self.oo_tree.selection()
        if not sel:
            messagebox.showinfo("Cancel", "Select an open order first.", parent=self)
            return
        v = self.oo_tree.item(sel[0], "values")
        order_id, symbol = int(v[0]), str(v[1])
        lines = [f"Cancel order {order_id} on {symbol}?", f"{v[2]} {v[3]} price {v[4]} qty {v[5]} filled {v[6]} status {v[7]}"]
        if not self._confirm("Confirm cancel", lines, ok_label="Cancel order"):
            return
        self._bg(lambda: self.actions.cancel_order(symbol, order_id, confirmed=True),
                 lambda r: (messagebox.showinfo("Cancelled", f"orderId {order_id} status {r.get('status')}", parent=self),
                            self.refresh_open_orders()))

    # ------------------------------------------------------------ withdrawals
    def load_coins(self) -> None:
        c = self._require_client()
        if not supports_wallet(c.host):
            messagebox.showinfo("Unavailable", "Wallet endpoints are not available on the spot testnet.", parent=self)
            return

        def done(payload: list) -> None:
            self.coins = parse_coins(payload)
            names = sorted(k for k, v in self.coins.items() if v.networks)
            self.wd_coin_cb.configure(values=names)
            self.log(f"loaded {len(names)} coins with network info from Binance")
        self._bg(c.coins_config, done)

    def _fill_networks(self) -> None:
        coin = self.coins.get(self.wd_coin.get())
        if not coin:
            return
        labels = []
        for n in coin.networks:
            flag = "enabled" if n.withdraw_enable else "DISABLED"
            labels.append(f"{n.network} | {n.name} | {flag} | fee {fmt(n.fee)} | min {fmt(n.withdraw_min)}"
                          + (" | memo required" if n.requires_memo else ""))
        self.wd_net_cb.configure(values=labels)
        self.wd_network.set("")
        self.wd_info.set(f"{coin.coin} free balance: {fmt(coin.free)}. Select a network.")

    def _selected_network(self) -> str:
        return self.wd_network.get().split("|")[0].strip()

    def _show_network_info(self) -> None:
        coin = self.coins.get(self.wd_coin.get())
        if not coin or not self.wd_network.get():
            return
        n = coin.network(self._selected_network())
        info = (f"{coin.coin} on {n.network} ({n.name}): withdrawEnable={n.withdraw_enable}, fee={fmt(n.fee)}, "
                f"min={fmt(n.withdraw_min)}, max={fmt(n.withdraw_max) if n.withdraw_max else 'n/a'}, "
                f"precision step={fmt(n.integer_multiple) if n.integer_multiple else 'n/a'}, "
                f"memo/tag required={n.requires_memo}. Free balance {fmt(coin.free)}.")
        if n.withdraw_desc:
            info += f"\nBinance note: {n.withdraw_desc}"
        if n.special_tips:
            info += f"\nBinance tip: {n.special_tips}"
        self.wd_info.set(info)

    def preview_withdrawal(self) -> None:
        c = self._require_client()
        if self.actions is None or not self.actions.withdrawals_enabled:
            messagebox.showinfo("Read-only", "Enable withdrawals on the Connect tab first.", parent=self)
            return
        coin = self.coins.get(self.wd_coin.get())
        if not coin or not self.wd_network.get():
            messagebox.showinfo("Withdraw", "Load coins, then choose a coin and network.", parent=self)
            return
        try:
            w = prepare_withdrawal(coin, self._selected_network(), self.wd_address.get(), self.wd_memo.get(),
                                   self.wd_amount.get())
        except LunaError as e:
            self._error(e, "Withdrawal")
            return
        lines = [
            f"Coin: {w.coin}", f"Network: {w.network} ({w.network_name})",
            f"Destination address: {w.address}", f"Memo / tag: {w.memo or '(none)'}",
            f"Amount: {fmt(w.amount)} {w.coin}", f"Network fee (from Binance): {fmt(w.fee)} {w.coin}",
            f"Expected received (amount - fee): {fmt(w.expected_received)} {w.coin}",
            f"Withdraw order id: {w.withdraw_order_id}",
            "", "Check the address character by character. Crypto withdrawals cannot be reversed.",
        ]
        if not self._confirm("Withdrawal preview", lines, ok_label="Continue"):
            self.log("withdrawal preview cancelled by user")
            return
        if not self._confirm("FINAL confirmation", [f"Send {fmt(w.amount)} {w.coin} via {w.network} to", w.address,
                                                     f"memo: {w.memo or '(none)'}"], require_text="WITHDRAW", ok_label="Submit withdrawal"):
            self.log("withdrawal final confirmation declined by user")
            return
        self._bg(lambda: self.actions.withdraw(w, confirmed=True),
                 lambda r: (messagebox.showinfo("Withdrawal submitted", f"Binance id {r.get('id')}", parent=self),
                            self.refresh_withdraw_history()))

    def refresh_withdraw_history(self) -> None:
        c = self._require_client()
        if not supports_wallet(c.host):
            return

        def done(hist: list) -> None:
            self.wd_tree.delete(*self.wd_tree.get_children())
            for h in hist:
                self.wd_tree.insert("", "end", values=(h.get("applyTime"), h.get("coin"), h.get("network"), h.get("amount"),
                                                       h.get("transactionFee"), WITHDRAW_STATUS.get(h.get("status"), h.get("status")),
                                                       h.get("address"), h.get("txId", "")))
        self._bg(lambda: c.withdraw_history(), done)

    # ------------------------------------------------------------ reconcile
    def reconcile(self) -> None:
        if self.actions is None:
            return
        self._bg(self.actions.reconcile,
                 lambda done: messagebox.showinfo("Reconcile", "\n".join(f"{u.description}\n -> {u.resolution}" for u in done)
                                                  or "Nothing could be resolved (see log).", parent=self))


def main() -> None:
    app = LunaApp()
    app.mainloop()
