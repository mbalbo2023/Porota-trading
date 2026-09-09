#!/usr/bin/env python3
"""RC6 IOL read-only client hotfix.

Authentication uses POST /token. Historical prices use IOL API v2 with the
route and path enums expected by the current service. This subclass only
changes read-only routing details and deliberately adds no order capability.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

import ak_iol_client as legacy


_MARKET_PATH = {
    "bcba": "bCBA",
    "nyse": "nYSE",
    "nasdaq": "nASDAQ",
    "amex": "aMEX",
    "bcs": "bCS",
    "rofex": "rOFX",
    "rofx": "rOFX",
}


class IOLClient(legacy.IOLClient):
    def _token_request(self, payload: dict, label: str) -> bool:
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
        """Historical daily bars through IOL's read-only v2 route.

        IOL's final path component is an enum (`ajustada` / `sinAjustar`), not
        a JSON-style boolean. Market names are normalized to the API's path
        spelling. No operational endpoint is called or used as fallback.
        """
        hasta = datetime.now().strftime("%Y-%m-%d")
        desde = (datetime.now() - timedelta(days=max(1, int(dias)))).strftime("%Y-%m-%d")
        market_key = str(mercado).strip().lower()
        market_path = _MARKET_PATH.get(market_key, str(mercado).strip())
        safe_symbol = quote(str(simbolo).strip(), safe="")
        safe_market = quote(market_path, safe="")
        series_kind = "ajustada" if ajustada else "sinAjustar"
        route = (
            f"/api/v2/{safe_market}/Titulos/{safe_symbol}/Cotizacion/seriehistorica/"
            f"{desde}/{hasta}/{series_kind}"
        )
        data = self._get(route, f"histórico {simbolo}")
        if isinstance(data, dict):
            return data.get("precios", []) or []
        if isinstance(data, list):
            return data
        return []
