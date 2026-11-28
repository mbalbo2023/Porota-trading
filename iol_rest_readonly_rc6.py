"""IOL REST market-data adapter for the existing RC6 SHADOW collector."""
from __future__ import annotations

from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE = "IOL_REST"
API = "https://api.iolinversiones.com"
CONFIG = Path("/opt/porota-trading/.env")
RESPONSE_LIMIT = 1024 * 1024
AUTH_RETRY_SECONDS = 10 * 60  # Existing pre-market guardian cadence.
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,39}\Z")
MARKETS = frozenset({"BCBA", "NYSE", "NASDAQ"})
CURRENCIES = frozenset({"ARS", "USD", "USD_MEP", "USD_CCL"})
ASSET_TYPES = {"TIT. PUBLICOS": "BONOS", "OBLIGACIONES NEGOCIABLES": "OBLIGACIONES",
               "FONDO COMÚN DE INVERSIÓN": "FCI"}


class IOLRestError(RuntimeError):
    def __init__(self, reason, *, status_code=None, retry_after=None, failure_class=None):
        super().__init__(reason)
        self.status_code = status_code
        self.retry_after = retry_after
        self.failure_class = failure_class


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _object(value):
    return value if isinstance(value, dict) else {}


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _aware_time(value):
    if isinstance(value, str) and len(value) <= 40:
        try:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if at.tzinfo is not None:
                return at.isoformat()
        except ValueError:
            pass
    return None


def _token_text(value):
    return isinstance(value, str) and re.fullmatch(r"[!-~]{16,16384}", value) is not None


class IOLRestReadOnly:
    """Shares a private REST token between the existing collector and health timer."""

    source = SOURCE

    def __init__(self, state_dir, *, config=CONFIG, allow_renew=True, governor=None,
                 clock=time.time, opener=None, owner_managed_auth=False):
        self.directory = Path(state_dir)
        self.store = self.directory / "session.json"
        self.config = Path(config)
        self.allow_renew = allow_renew
        self.owner_managed_auth = owner_managed_auth and allow_renew
        self.governor = governor
        self.clock = clock
        self.opener = opener if opener is not None else build_opener(_NoRedirect)
        self.active = None
        self.auth_blocked = False
        self.wire_observer = None

    @property
    def uses_wire_budget(self):
        return self.governor is not None

    @property
    def session_current(self):
        """Inspect the owner's token state without logging in or exposing it."""
        cached = self.active
        return (not self.auth_blocked and cached is not None
                and self._current(cached, self.clock()))

    def stream_bearer(self):
        """Give the in-process market stream the current owner's bearer without a grant."""
        cached = self.active
        if not self.auth_blocked and cached is not None and self._current(cached, self.clock()):
            return cached["access_token"]
        return None

    def _request(self, path, *, query=None, auth=None, token=None):
        headers = {"Accept": "application/json", "User-Agent": "Porota-Trading-RC6 (IOL REST market data)"}
        if auth is not None:
            if path != "/auth/token" or token is not None or query is not None:
                raise PermissionError("IOL_REST_AUTH_ROUTE_DENIED")
            headers["Content-Type"] = "application/json"
        elif not path.startswith(("/broker/market-data/", "/broker/assets/")) or not _token_text(token):
            raise PermissionError("IOL_REST_MARKET_ROUTE_DENIED")
        else:
            headers["Authorization"] = "Bearer " + token
        request = Request(API + path + ("?" + urlencode(query) if query else ""),
                          data=json.dumps(auth).encode() if auth is not None else None, headers=headers)
        if self.governor is not None:
            self.governor.acquire()
        started = time.monotonic()
        status = "TRANSPORT_ERROR"
        body_bytes = None
        try:
            with self.opener.open(request, timeout=8) as response:
                code = getattr(response, "status", None)
                status = str(code) if type(code) is int else "HTTP_STATUS_UNAVAILABLE"
                raw = response.read(RESPONSE_LIMIT + 1)
                body_bytes = len(raw)
        except HTTPError as exc:
            with exc:
                status = str(exc.code)
                retry = exc.headers.get("Retry-After", "")
                retry_after = int(retry) if re.fullmatch(r"[0-9]{1,6}", retry) else None
                if exc.code == 429 and self.governor is not None:
                    self.governor.trip(retry_after)
                raise IOLRestError("IOL_REST_HTTP_ERROR", status_code=exc.code,
                                   retry_after=retry_after) from None
        except (URLError, TimeoutError, OSError):
            raise IOLRestError("IOL_REST_TRANSPORT_UNAVAILABLE", failure_class="TRANSPORT") from None
        finally:
            if self.wire_observer is not None:
                endpoint = "oauth" if auth is not None else (
                    "asset" if path.startswith("/broker/assets/") else path.rsplit("/", 1)[-1])
                self.wire_observer(endpoint, started, time.monotonic(), status, body_bytes)
        try:
            if len(raw) > RESPONSE_LIMIT:
                raise ValueError
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("error") is not None or payload.get("data") is None:
                raise ValueError
            return payload["data"]
        except (UnicodeError, ValueError):
            raise IOLRestError("IOL_REST_RESPONSE_UNVERIFIED") from None

    def _credentials(self):
        from scripts.porota_rootless_host import Hold, parse_env, read_regular

        try:
            config = parse_env(read_regular(self.config).decode("utf-8-sig"))
        except (Hold, OSError, TypeError, ValueError):
            raise IOLRestError("IOL_REST_CONFIG_UNAVAILABLE", status_code=401) from None
        username, password = config.get("IOL_USERNAME"), config.get("IOL_PASSWORD")
        if config.get("IOL_ENABLED", "").lower() != "true" or not username or not password:
            raise IOLRestError("IOL_REST_CREDENTIALS_UNCONFIGURED", status_code=401)
        return {"grant_type": "password", "username": username, "password": password}

    def _cached(self):
        from scripts.porota_rootless_host import Hold, read_regular

        try:
            status = self.store.lstat()
        except FileNotFoundError:
            return {}
        if status.st_uid != os.geteuid() or status.st_mode & 0o077 or status.st_nlink != 1:
            raise IOLRestError("IOL_REST_TOKEN_CUSTODY_UNVERIFIED", status_code=401)
        try:
            return _object(json.loads(read_regular(self.store, 65536)))
        except (Hold, OSError, UnicodeError, ValueError):
            raise IOLRestError("IOL_REST_TOKEN_CUSTODY_UNVERIFIED", status_code=401) from None

    @staticmethod
    def _current(cached, now):
        return (_token_text(cached.get("access_token")) and _positive(cached.get("renew_at"))
                and now < cached["renew_at"])

    def _session(self, now):
        from scripts.porota_rootless_host import private_dir, user_lock, write_json

        if not self.allow_renew:
            cached = self._cached()
            if self._current(cached, now):
                return cached
            raise IOLRestError("IOL_REST_AUTH_ON_HOLD", status_code=401)

        private_dir(self.directory)
        with user_lock(self.directory / "session.lock"):
            cached = self._cached()
            if self._current(cached, now):
                return cached
            if _positive(cached.get("retry_at")) and now < cached["retry_at"]:
                raise IOLRestError("IOL_REST_AUTH_ON_HOLD", status_code=401)
            refresh = cached.get("refresh_token")
            try:
                if _token_text(refresh):
                    try:
                        data = self._request("/auth/token", auth={"grant_type": "refresh_token", "refresh_token": refresh})
                    except IOLRestError as error:
                        if error.status_code not in (400, 401):
                            raise
                        data = self._request("/auth/token", auth=self._credentials())
                else:
                    data = self._request("/auth/token", auth=self._credentials())
                data = _object(data)
                access, refresh, seconds = data.get("access_token"), data.get("refresh_token"), data.get("expires_in")
                if not _token_text(access) or not _token_text(refresh) or not _positive(seconds) or seconds > 86400:
                    raise IOLRestError("IOL_REST_TOKEN_UNVERIFIED", status_code=401)
            except IOLRestError as error:
                write_json(self.store, {"retry_at": now + max(AUTH_RETRY_SECONDS, error.retry_after or 0)})
                raise
            state = {"access_token": access, "refresh_token": refresh,
                     "renew_at": now + seconds - min(60, seconds / 4)}
            if self.owner_managed_auth and _positive(cached.get("reauth_after")):
                state["reauth_after"] = cached["reauth_after"]
            write_json(self.store, state)
            return state

    def authenticate(self):
        if self.auth_blocked:
            raise IOLRestError("IOL_REST_AUTH_ON_HOLD", status_code=401)
        now = self.clock()
        if self.active is None or now >= self.active["renew_at"]:
            try:
                self.active = self._session(now)
            except IOLRestError:
                if not self.owner_managed_auth:
                    self.auth_blocked = True
                raise

    def _invalidate_owned_access(self):
        from scripts.porota_rootless_host import Hold, user_lock, write_json

        rejected = self.active["access_token"]
        self.active = None
        now = self.clock()
        try:
            with user_lock(self.directory / "session.lock"):
                cached = self._cached()
                if cached.get("access_token") != rejected:
                    return  # preserve a newer session
                previous = cached.get("reauth_after")
                if _positive(previous) and previous > now:
                    state = {"refresh_token": cached["refresh_token"],
                             "reauth_after": previous, "retry_at": previous}
                else:
                    state = {"refresh_token": cached["refresh_token"],
                             "reauth_after": now + AUTH_RETRY_SECONDS}
                write_json(self.store, state)
        except (Hold, OSError, IOLRestError, KeyError):
            # The next request must not replay a token whose invalidation
            # could not be durably written by this owner.
            self.auth_blocked = True

    def _get(self, path, params):
        self.authenticate()
        try:
            return self._request(path, query=params, token=self.active["access_token"])
        except IOLRestError as error:
            if self.owner_managed_auth and error.status_code == 401:
                self._invalidate_owned_access()
            elif not self.owner_managed_auth and error.status_code in (401, 403):
                self.auth_blocked = True
            raise

    def read_market_data(self, endpoint, symbol, market, term):
        """Return one provider response without rebuilding or merging its clocks."""
        if (endpoint not in {"quote", "depth"} or not isinstance(symbol, str)
                or not SYMBOL.fullmatch(symbol) or market not in MARKETS
                or term not in {"T0", "T1"}):
            raise IOLRestError("IOL_REST_MARKET_IDENTITY_UNSUPPORTED")
        return self._get(f"/broker/market-data/{quote(symbol, safe='')}/{endpoint}",
                         {"market": market, "term": term})

    def read_daily_bars(self, symbol, market, start, end):
        if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or market not in MARKETS
                or type(start) is not date or type(end) is not date or not 0 <= (end - start).days <= 365):
            raise IOLRestError("IOL_REST_HISTORICAL_QUERY_UNSUPPORTED")
        result = self._get(f"/broker/market-data/{quote(symbol, safe='')}/bars",
                           {"market": market, "from": f"{start} 00:00:00", "to": f"{end} 23:59:59"})
        if (not isinstance(result, dict) or (result.get("symbol"), result.get("market")) != (symbol, market)
                or not isinstance(result.get("bars"), list)
                or type(result.get("total_bars")) is not int
                or result["total_bars"] != len(result["bars"]) or len(result["bars"]) > 366):
            raise IOLRestError("IOL_REST_HISTORICAL_RESPONSE_UNVERIFIED")
        return result

    def _quote(self, symbol, market, term):
        data = _object(self.read_market_data("quote", symbol, market, term))
        if (data.get("symbol"), str(data.get("market", "")).upper(),
                str(data.get("settlement_term", "")).upper()) != (symbol, market, term):
            raise IOLRestError("IOL_REST_QUOTE_IDENTITY_UNVERIFIED")
        quote_at = _aware_time(data.get("last_updated"))
        result = {"last": _object(data.get("prices")).get("current"),
                  "variation_pct": _object(data.get("change")).get("percent"),
                  "cash_volume": _object(data.get("volume")).get("amount"),
                  "provider_observed_at": quote_at,
                  "currency": data.get("currency") if data.get("currency") in CURRENCIES else None}
        try:
            depth = _object(self.read_market_data("depth", symbol, market, term))
        except IOLRestError as error:
            if error.status_code in (401, 403, 429):
                raise
            return result
        book_at = _aware_time(depth.get("last_updated"))
        if (depth.get("symbol"), str(depth.get("market", "")).upper(),
                str(depth.get("settlement_term", "")).upper()) == (symbol, market, term):
            if depth.get("currency") and depth["currency"] != result["currency"]:
                return result
            result["provider_book_at"] = book_at
            for side, key in (("bid", "bids"), ("ask", "asks")):
                levels = depth.get(key)
                if isinstance(levels, list) and levels:
                    top = _object(levels[0])
                    if _positive(top.get("price")) and _positive(top.get("size")):
                        result[side] = top["price"]
                        result[side + "_size"] = top["size"]
        return result

    def _asset(self, symbol, market):
        data = _object(self._get(f"/broker/assets/{quote(symbol, safe='')}", {"market": market}))
        if data.get("symbol") != symbol or str(data.get("market", "")).upper() != market:
            raise IOLRestError("IOL_REST_ASSET_IDENTITY_UNVERIFIED")
        trading = _object(data.get("trading_info"))
        raw_type = str(data.get("asset_type") or data.get("type") or "")
        return {"symbol": symbol, "market": market, "currency": data.get("currency"),
                "type": ASSET_TYPES.get(raw_type.strip().upper(), raw_type),
                "provider_asset_type": raw_type,
                "description": data.get("description") or data.get("name"),
                "units_per_lot": data.get("units_per_lot"),
                "term": data.get("default_settlement_term"),
                "provider_trading_info": {key: trading[key] for key in
                    ("lot_size", "minimum_quantity", "minimum_investment") if _positive(trading.get(key))}}

    def _options(self, symbol, market):
        data = self._get(f"/broker/market-data/{quote(symbol, safe='')}/options", {"market": market})
        if not isinstance(data, list):
            raise IOLRestError("IOL_REST_OPTIONS_UNVERIFIED")
        rows = []
        for item in data:
            item = _object(item)
            if not SYMBOL.fullmatch(str(item.get("symbol", ""))):
                continue
            fields = _object(item.get("option"))
            book = _object(item.get("book"))
            quotes = _object(item.get("quotes"))
            rows.append({"symbol": item["symbol"],
                         "option_type": fields.get("type"),
                         "strike_price": fields.get("strike_price"),
                         "expiration": fields.get("expiration_date") or fields.get("expiration"),
                         "volume": quotes.get("volume") if _positive(quotes.get("volume")) else None,
                         "is_stale": True,  # No native market timestamp in this response.
                         "bid_price": _object(book.get("bid")).get("price"),
                         "ask_price": _object(book.get("ask")).get("price")})
        return {"underlying": symbol, "options": rows}

    def _repos(self, currency):
        terms = self._get("/broker/market-data/repos/terms", {"currency": currency})
        if not isinstance(terms, list):
            raise IOLRestError("IOL_REST_REPO_TERMS_UNVERIFIED")
        result = []
        for days in (1, 2, 3):
            rows = [_object(row) for row in terms]
            matches = [row for row in rows if row.get("term_days") == days
                       and type(row.get("term_id")) is int and row["term_id"] > 0]
            if len(matches) != 1:
                continue
            term = matches[0]
            book = self._get("/broker/market-data/repos/quotes", {"currency": currency, "term_id": term["term_id"]})
            if not isinstance(book, list) or not book:
                continue
            bid = _object(_object(book[0]).get("bid"))
            if not _positive(bid.get("rate")) or not _positive(bid.get("amount")):
                continue
            result.append({"term_days": days, "side": "COLOCADORA", "currency": currency,
                           "due_date": term.get("due_date"), "minimum_amount": term.get("min_amount"),
                           "tna": bid["rate"], "available_amount": bid["amount"],
                           # The published envelope has no per-book provider timestamp.
                           "reference_only": True, "transport": SOURCE})
        return {"rates": result}

    def call(self, name, arguments):
        if name not in {"get_asset_quote", "get_asset_info", "get_options_chain", "get_caucion_rates"}:
            raise IOLRestError("IOL_REST_CAPABILITY_UNVERIFIED")
        if name == "get_caucion_rates":
            currency = arguments.get("currency")
            if currency not in ("ARS", "USD") or arguments.get("caucion_type") != "colocadora":
                raise IOLRestError("IOL_REST_MARKET_IDENTITY_UNSUPPORTED")
            return self._repos(currency)
        symbol = str(arguments.get("symbol", "")).upper()
        market = str(arguments.get("market", "BCBA")).upper()
        if not SYMBOL.fullmatch(symbol) or market not in MARKETS:
            raise IOLRestError("IOL_REST_MARKET_IDENTITY_UNSUPPORTED")
        if name == "get_asset_info":
            return self._asset(symbol, market)
        if name == "get_options_chain":
            return self._options(symbol, market)
        term = str(arguments.get("term", "")).upper()
        if term not in {"T0", "T1"}:
            raise IOLRestError("IOL_REST_MARKET_IDENTITY_UNSUPPORTED")
        return self._quote(symbol, market, term)
