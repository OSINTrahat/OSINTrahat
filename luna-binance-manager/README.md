# Luna Binance Manager

A local Windows desktop app (Python 3 + Tkinter, standard library only) for managing
**your own** Binance spot account: balances, open orders, trade history, manual spot
buy/sell/cancel, and manual crypto withdrawals. It starts in **read-only mode** and
every trade or withdrawal needs an explicit confirmation. There is no automated or
scheduled trading/withdrawing, no telemetry, no cloud, no remote control.

Bangla setup guide: [SETUP_BN.md](SETUP_BN.md)

## Run

1. Install Python 3.10+ from python.org (keep "tcl/tk and IDLE" ticked).
2. Double-click `run_luna.bat` (or `python main.py`).
3. Enter your API key + secret in the app (masked). They are kept in memory only and
   wiped on *Disconnect & wipe* or when the window closes.

Tests: `run_tests.bat` (or `python -m pytest`).

## Binance API key setup (done on binance.com, never by this app)

| Feature | Binance API key permission | Notes |
|---|---|---|
| Balances / orders / history | Enable Reading | enough for read-only |
| Manual spot buy/sell/cancel | Enable Spot & Margin Trading | enable the *Enable manual spot trading* switch inside the app |
| Withdrawals | Enable Withdrawals | Binance only offers this when the key is restricted to trusted IPs. You need a **fixed public IP** on the key. API withdrawals also only go to addresses on your **Withdrawal Address Whitelist**. |

The app reads `GET /sapi/v1/account/apiRestrictions` after connecting and greys out
the withdrawal switch when the key has no withdrawal permission.

## Endpoints used (all documented, HTTPS with TLS verification, host allowlist)

Hosts: `api.binance.com`, `api-gcp.binance.com`, `api1..4.binance.com`, and the spot
testnet `testnet.binance.vision` (no wallet endpoints there).

`/api/v3/time`, `/api/v3/exchangeInfo`, `/api/v3/ticker/price`, `/api/v3/account`,
`/api/v3/openOrders`, `/api/v3/myTrades`, `/api/v3/order` (GET/POST/DELETE),
`/api/v3/order/test`, `/sapi/v1/account/apiRestrictions`,
`/sapi/v1/capital/config/getall`, `/sapi/v1/capital/withdraw/apply`,
`/sapi/v1/capital/withdraw/history`.

## Safety design

- **Read-only by default**; trading and withdrawals are separate switches with their
  own confirmation (withdrawals require typing `ENABLE`).
- **Preview + confirm** for every order and cancel; **preview + second typed
  confirmation (`WITHDRAW`)** for every withdrawal.
- `Decimal` everywhere; orders validated against the symbol's `PRICE_FILTER`,
  `LOT_SIZE`, `MARKET_LOT_SIZE`, `NOTIONAL`/`MIN_NOTIONAL`, then checked with
  `POST /api/v3/order/test` before the confirmation dialog.
- Withdrawal networks, availability, fee, min/max, precision
  (`withdrawIntegerMultiple`), address regex and memo requirement all come from
  `GET /sapi/v1/capital/config/getall`. Nothing is guessed.
- **One action in flight at a time** (double-click protection).
- **No automatic retries.** A timeout, HTTP 5xx or `-1007` marks the action
  *UNKNOWN*; all further actions are blocked until *Reconcile* queries Binance
  (`GET /api/v3/order` by `origClientOrderId`, or withdraw history by
  `withdrawOrderId`).
- Secrets never go to logs, URLs, source or error messages (a redactor scrubs every
  message; the key travels only in the `X-MBX-APIKEY` header, the secret only
  signs in memory).

## Tested vs untested

**Tested with mocks (55 tests, no network, no real credentials):** HMAC signing
against the official doc example, host allowlist, header/body placement, secret
redaction, order filter validation, withdrawal validation (networks, regexes,
memo, min/max/precision, balance), read-only gate, confirmation gate, duplicate
prevention, timeout/5xx/-1007 → UNKNOWN → reconcile, GUI smoke flows (connect,
preview, withdrawal gating) under a virtual X display on Linux.

**Not tested (requires your live account / Windows):** real Binance responses
(permissions, travel-rule prompts, address whitelist rejection), real order fills,
real withdrawals, the `.bat` launchers on Windows, and Windows Tk rendering. Use
the spot testnet for orders first. The app never fakes a successful connection
or transaction: every result shown comes from a Binance response.

Expected received amount is shown as `amount − withdrawFee` (Binance deducts the
network fee from the requested amount). Always verify on the Binance confirmation
e-mail/app before relying on it.
