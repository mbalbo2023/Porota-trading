#!/usr/bin/env python3
"""RC6 IOL read-only client hotfix.

IOL's current official API documentation authenticates with POST /token.
The legacy Porota client tried /api/v2/token first and only fell back on 404,
so an HTTP 401 on that non-canonical path prevented trying the documented
endpoint.  This subclass changes only token endpoint selection; all transport,
rate limiting and read-only market-data methods remain inherited.
"""
from __future__ import annotations

import time
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
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
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
            # unavailable at the HTTP routing layer. A 400/401 from /token is
            # authoritative and must not be hidden by probing alternatives.
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
        expires_in = int(data.get("expires_in", 900))
        self._expires_at = time.time() + expires_in
        legacy.logger.info("IOL %s exitoso; token válido por %d segundos.", label, expires_in)
        return bool(self._access_token)
