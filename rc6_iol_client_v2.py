#!/usr/bin/env python3
"""RC6 IOL read-only client hotfix.

IOL's current API authenticates with POST /token and exposes historical
series under /api/{mercado}/Titulos/{simbolo}/Cotizacion/seriehistorica/...
(without the legacy /api/v2 prefix used by ak_iol_client.py).

This subclass changes only those read-only routing details. It deliberately
adds no order, cancel, estimate, portfolio, or execution capability.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

import ak_iol_client as legacy


class IOLClient(legacy.IOLClient):
    def _token_request(self, payload: dict, label: str) -> bool:
        # Canonical endpoint first, compatibility path second.
        routes = ["/token", "/api/v2/token"]
        response = None
        for idx, route in enumerate(routes):
            try:
                candidate = requests.post(
                    f"{legacy.IOL_BASE_URL}{route}",
                    data=payload,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json,text/plain,*/*",
                        # The API currently sits behind Cloudflare. These are
                        # ordinary HTTP client headers, never credential data.
                        "User-Agent": "PorotaTrading-RC6-ReadOnly/1.0",
                    },
                    timeout=legacy.IOL_TIMEOUT,
                )
            except requests.RequestException as exc:
                legacy.logger.error("IOL %s en %s: fallo de red: %s", label, route, exc)
                continue
            response = candidate
            if candidate.status_code == 200:
                if idx:
                    legacy.logger.info("IOL token respondió por ruta de compatibilidad %s.", route)
                break
            # Only try the compatibility route when the canonical endpoint is
            # unavailable at the HTTP routing layer. A 400/401/403 from /token
            # is authoritative and must not be hidden by probing alternatives.
            if idx == 0 and candidate.status_code in {404, 405}:
                continue
            break

        if response is None:
            return False
        if response.status_code != 200:
            legacy.logger.error("IOL %s rechazado con HTTP %s.", label, response.status_code)
            return False

        data = response.json()
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        expires_in = int(data.get("expires_in", 1200))
        self._expires_at = time.time() + expires_in
        legacy.logger.info("IOL %s exitoso; token válido por %d segundos.", label, expires_in)
        return bool(self._access_token)

    def get_serie_historica(
        self,
        simbolo: str,
        mercado: str = "bcba",
        dias: int = 365,
        ajustada: bool = True,
    ) -> list[dict]:
        """Historical daily series through IOL's current read-only route.

        Important: unlike the legacy connector, the historical endpoint is
        /api/{mercado}/Titulos/... and not /api/v2/{mercado}/Titulos/....
        No fallback to any operational endpoint exists here.
        """
        hasta = datetime.now().strftime("%Y-%m-%d")
        desde = (datetime.now() - timedelta(days=max(1, int(dias)))).strftime("%Y-%m-%d")
        safe_symbol = quote(str(simbolo).strip(), safe="")
        safe_market = quote(str(mercado).strip().lower(), safe="")
        route = (
            f"/api/{safe_market}/Titulos/{safe_symbol}/Cotizacion/seriehistorica/"
            f"{desde}/{hasta}/{'true' if ajustada else 'false'}"
        )
        data = self._get(route, f"histórico {simbolo}")
        if isinstance(data, dict):
            return data.get("precios", []) or []
        if isinstance(data, list):
            return data
        return []
