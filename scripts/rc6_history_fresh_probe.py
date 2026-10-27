"""Offhost, bounded source probe; no host/database migration or trading calls.

Keep the private SQLite capture on the disposable runner. Only the sanitized
receipt is suitable for an Actions artifact. A probe is not a history-v2
generation and cannot certify PIT, monetary identity or retention of legacy.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.rc6_history_fresh_sidecar import capture, preflight, preflight_destination


def _fetcher(source):
    if source == "PPI":
        from bd_ppi_readonly_guard import ProductionMarketReader
        key, secret = os.getenv("PPI_API_KEY"), os.getenv("PPI_API_SECRET")
        if not key or not secret:
            return None, None
        reader = ProductionMarketReader(key, secret, consumer="RC6_FRESH_OFFHOST",
                                        priority="DISCOVERY")
        reader.login_once()

        def fetch(target, from_day, to_day):
            symbol, family, _market, _currency, settlement = target
            return reader.history(symbol, family, settlement, from_day, to_day)

        return fetch, reader.close
    if source == "IOL":
        import ak_iol_client as iol
        client = iol.IOLClient()
        if not client.enabled:
            return None, None
        markets = {"BCBA": "bcba", "NYSE": "nyse", "NASDAQ": "nasdaq", "AMEX": "amex"}

        def fetch(target, from_day, to_day):
            symbol, _family, market, _currency, _settlement = target
            if market not in markets:
                raise ValueError("FRESH_IOL_MARKET_UNSUPPORTED")
            route = (f"/api/v2/{markets[market]}/Titulos/{symbol}/Cotizacion/seriehistorica/"
                     f"{from_day.isoformat()}/{to_day.isoformat()}/false")
            return client._get(route, "fresh historical evidence")

        return fetch, lambda: None
    if source == "A3_CEM":
        from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient
        client = A3CEMPublicReadOnlyClient()

        def fetch(target, from_day, to_day):
            symbol, family, market, *_ = target
            if family not in {"FUTUROS", "OPCIONES"} or market not in {"A3", "ROFEX"}:
                raise ValueError("FRESH_A3_IDENTITY_UNSUPPORTED")
            return client.closing_prices(symbol=symbol, date_from=from_day.isoformat(),
                                         date_to=to_day.isoformat(), page=1, page_size=500)

        return fetch, client.session.close
    if source == "DATA912":
        import ba_data912_history as data912
        if data912.DATA912_EXECUTION_ALLOWED:
            raise RuntimeError("FRESH_EXECUTION_INVARIANT_BROKEN")
        session = data912._session()

        def fetch(target, _from_day, _to_day):
            symbol, family, *_ = target
            if family not in data912.OPERATIONAL_FAMILIES:
                raise ValueError("FRESH_DATA912_FAMILY_UNSUPPORTED")
            return data912._get_json(session, data912.GROUPS[family][1].format(ticker=symbol))

        return fetch, session.close
    raise ValueError("FRESH_SOURCE_ADAPTER_UNAVAILABLE")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        choices=["PPI", "IOL", "DATA912", "A3_CEM"])
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.targets.stat().st_size > 100_000:
        raise SystemExit("FRESH_TARGET_MANIFEST_TOO_LARGE")
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    if not isinstance(targets, list):
        raise SystemExit("FRESH_TARGET_MANIFEST_INVALID")
    targets = preflight(targets, args.source)
    if not args.receipt.parent.is_dir():
        raise SystemExit("FRESH_RECEIPT_PARENT_MISSING")
    preflight_destination(args.receipt, protected_paths=(args.targets, args.sidecar))
    preflight_destination(args.sidecar, protected_paths=(args.targets, args.receipt))
    fetch, close = _fetcher(args.source)
    if fetch is None:
        result = {"state": "NO_VERIFICADO", "reason": "FRESH_READONLY_CREDENTIALS_ABSENT",
                  "source": args.source, "provider_requests": 0,
                  "history_v2_writes": 0, "execution_allowed": False}
    else:
        try:
            result = capture(args.sidecar, targets, args.source, fetch,
                             protected_paths=(args.targets, args.receipt))
            result['provider_requests'] = result.pop('fetch_invocations')
        finally:
            close()
    result.pop("generation_path", None)
    args.receipt.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n",
                            encoding="utf-8")
    args.receipt.chmod(0o600)
    print(json.dumps({"state": result["state"], "source": args.source,
                      "provider_requests": result["provider_requests"],
                      "history_v2_writes": 0, "execution_allowed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
