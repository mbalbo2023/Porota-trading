#!/usr/bin/env python3
"""RC6 PPI web trusted-profile reauthentication helper.

Purpose: renew an expired PPI web session using credentials already stored on
host, without exposing them and without visiting trading/order routes.

Safety contract:
- no DB access, broker imports, orders, quantities or prices;
- credentials are read only from a local secret file and never printed/written;
- navigation is restricted to PPI account/login and trading landing pages;
- POST is permitted only to the exact PPI authentication endpoint observed in
  the public login bundle: /api/Seguridad/Auth/Login;
- third-party POST telemetry/ads is aborted silently;
- any other first-party mutation remains fail-closed and is reported sanitized;
- if a real OTP/PIN/2FA code is required, fail closed and never automate it;
- after successful login this helper exits; Contract Evidence collection remains
  a separate process.

Diagnostics are deliberately sanitized: request bodies, cookies, credentials,
tokens and response values are never emitted. For the approved login response we
retain only HTTP status and boolean/schema signals needed to classify auth.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

LOGIN_URL = "https://cuenta.portfoliopersonal.com/login"
TRADING_ROOT = "https://trading.portfoliopersonal.com/"
ALLOWED_PAGE_HOSTS = {"cuenta.portfoliopersonal.com", "trading.portfoliopersonal.com"}
LOGIN_API_KEYS = {
    ("cuenta.portfoliopersonal.com", "/api/Seguridad/Auth/Login"),
    ("api.portfoliopersonal.com", "/api/Seguridad/Auth/Login"),
}
APPROVED_AUTH_POSTS = set(LOGIN_API_KEYS)
ORDER_PATH_HINT = re.compile(r"(^|/)(operar|orden|orders?|trade|confirm|cancel)(/|$)", re.I)
OTP_TEXT_HINT = re.compile(
    r"(pin|otp|token|c[oó]digo).{0,120}(mail|correo|email|verific|seguridad|autentic)|"
    r"(mail|correo|email).{0,120}(pin|otp|token|c[oó]digo)|segundo factor|"
    r"doble factor|autenticaci[oó]n de dos factores",
    re.I | re.S,
)
TRUST_PROMPT_HINT = re.compile(
    r"dispositivo.{0,80}(confianza|confiable|seguro)|"
    r"(confiar|recordar|verificar).{0,80}dispositivo",
    re.I | re.S,
)
NOW_NOT_HINT = re.compile(r"ahora\s+no|no\s+ahora|m[aá]s\s+tarde|omitir", re.I)


def clean_url(value: str) -> str:
    u = urlsplit(str(value))
    return f"{u.scheme}://{u.netloc}{u.path}"


def clean_host_path(value: str) -> str:
    u = urlsplit(str(value))
    return f"{u.netloc}{u.path}"[:240]


def safe_page_url(value: str) -> bool:
    u = urlsplit(str(value))
    if u.scheme != "https" or u.netloc not in ALLOWED_PAGE_HOSTS:
        return False
    return ORDER_PATH_HINT.search(u.path.lower()) is None


def authenticated_url(value: str) -> bool:
    u = urlsplit(str(value))
    return (
        u.scheme == "https"
        and u.netloc == "trading.portfoliopersonal.com"
        and "login" not in u.path.lower()
        and "logout" not in u.path.lower()
        and ORDER_PATH_HINT.search(u.path.lower()) is None
    )


def parse_secret(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values.get("PPI_WEB_USERNAME", ""), values.get("PPI_WEB_PASSWORD", "")


def body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)[:20000]
    except Exception:
        return ""


def first_visible(page, selectors):
    for selector in selectors:
        try:
            q = page.locator(selector)
            for idx in range(min(q.count(), 12)):
                node = q.nth(idx)
                if node.is_visible():
                    return node
        except Exception:
            pass
    return None


def trust_prompt_present(page) -> bool:
    text = body_text(page)
    return bool(TRUST_PROMPT_HINT.search(text) and NOW_NOT_HINT.search(text))


def otp_required(page) -> bool:
    if trust_prompt_present(page):
        return False
    text = body_text(page)
    if OTP_TEXT_HINT.search(text):
        return True
    candidates = [
        "input[autocomplete='one-time-code']", "input[name*='pin' i]", "input[id*='pin' i]",
        "input[name*='otp' i]", "input[id*='otp' i]", "input[name*='token' i]",
        "input[id*='token' i]", "input[name*='code' i]", "input[id*='code' i]",
    ]
    return first_visible(page, candidates) is not None


def status_payload(status: str, *, attempts: int = 0, blocked_post_path: str = "",
                   stage: str = "", page_url: str = "", auth_observation: dict | None = None) -> str:
    payload = {
        "status": status,
        "attempts": attempts,
        "credentials_exposed": False,
        "orders_visited": False,
        "real_orders_sent": 0,
    }
    if blocked_post_path:
        payload["blocked_post_path"] = blocked_post_path[:240]
    if stage:
        payload["stage"] = stage[:80]
    if page_url:
        payload["page_url"] = clean_url(page_url)[:240]
    if auth_observation:
        payload["auth_http_status"] = auth_observation.get("http_status")
        payload["auth_json_object"] = bool(auth_observation.get("json_object"))
        payload["auth_has_token_shape"] = bool(auth_observation.get("has_token"))
        payload["auth_twofa_shape"] = bool(auth_observation.get("twofa"))
        payload["auth_change_password_shape"] = bool(auth_observation.get("change_password"))
        payload["auth_safe_keys"] = auth_observation.get("safe_keys", [])[:20]
    return json.dumps(payload, sort_keys=True)


def classify_auth_observation(obs: dict) -> str | None:
    if not obs:
        return None
    status = int(obs.get("http_status") or 0)
    if obs.get("twofa"):
        return "BLOCKED_AUTH_2FA_REQUIRED"
    if obs.get("change_password"):
        return "BLOCKED_AUTH_PASSWORD_CHANGE_REQUIRED"
    if status == 429:
        return "BLOCKED_AUTH_RATE_LIMITED"
    if status >= 500:
        return "BLOCKED_AUTH_SERVER_ERROR"
    if status in {400, 401}:
        return "BLOCKED_AUTH_CREDENTIALS_REJECTED_OR_CHALLENGE"
    if status == 403:
        return "BLOCKED_AUTH_FORBIDDEN_OR_CHALLENGE"
    if 200 <= status < 300 and obs.get("has_token"):
        return "AUTH_API_SUCCESS_NO_TRADING_SESSION"
    if status:
        return "BLOCKED_AUTH_RESPONSE_UNCLASSIFIED"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()
    profile = Path(args.profile)
    secret = Path(args.secret)
    if not profile.is_dir():
        print(status_payload("BLOCKED_AUTH_PROFILE_MISSING")); return 4
    if not secret.is_file():
        print(status_payload("BLOCKED_AUTH_LOCAL_SECRET_MISSING")); return 4
    user, password = parse_secret(secret)
    if not user or not password:
        print(status_payload("BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE")); return 4
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except Exception:
        print(status_payload("BLOCKED_PLAYWRIGHT_UNAVAILABLE")); return 4

    attempts = 0
    blocked_post_path = ""
    stage = "START"
    page = None
    auth_observation: dict = {}
    try:
        with sync_playwright() as pw:
            stage = "LAUNCH_PERSISTENT_CONTEXT"
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(8000)
            page.set_default_navigation_timeout(15000)

            def guard(route, request):
                nonlocal blocked_post_path
                method = request.method.upper()
                u = urlsplit(request.url)
                if u.scheme != "https":
                    return route.abort()
                if method in {"GET", "HEAD", "OPTIONS"}:
                    if u.netloc in ALLOWED_PAGE_HOSTS and ORDER_PATH_HINT.search(u.path.lower()):
                        return route.abort()
                    return route.continue_()
                key = (u.netloc, u.path.rstrip("/") or "/")
                if method == "POST" and key in APPROVED_AUTH_POSTS:
                    return route.continue_()
                if method == "POST" and u.netloc not in ALLOWED_PAGE_HOSTS:
                    return route.abort()
                blocked_post_path = clean_host_path(request.url)
                return route.abort()

            def observe_response(response):
                nonlocal auth_observation
                try:
                    req = response.request
                    u = urlsplit(response.url)
                    key = (u.netloc, u.path.rstrip("/") or "/")
                    if req.method.upper() != "POST" or key not in LOGIN_API_KEYS:
                        return
                    obs = {
                        "http_status": int(response.status),
                        "json_object": False,
                        "has_token": False,
                        "twofa": False,
                        "change_password": False,
                        "safe_keys": [],
                    }
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            obs["json_object"] = True
                            safe_names = {"twoFAInfo", "changePassword", "token", "usuario", "user", "error", "errors", "message", "status"}
                            obs["safe_keys"] = sorted(k for k in data.keys() if k in safe_names)
                            obs["twofa"] = bool(data.get("twoFAInfo"))
                            obs["change_password"] = bool(data.get("changePassword"))
                            obs["has_token"] = bool(data.get("token") or data.get("accessToken"))
                    except Exception:
                        pass
                    auth_observation = obs
                except Exception:
                    pass

            ctx.route("**/*", guard)
            page.on("response", observe_response)

            def goto_fast(url: str, label: str) -> None:
                nonlocal stage
                stage = label
                page.goto(url, wait_until="commit", timeout=15000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=7000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(700)

            goto_fast(TRADING_ROOT, "OPEN_TRADING_ROOT")
            if authenticated_url(page.url):
                ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=0)); return 0

            goto_fast(LOGIN_URL, "OPEN_LOGIN")
            attempts = 1
            stage = "AUTH_LOOP_1"
            if not safe_page_url(page.url):
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_UNEXPECTED_PAGE", attempts=attempts,
                                                  blocked_post_path=blocked_post_path,
                                                  stage=stage, page_url=final_url)); return 4

            if trust_prompt_present(page):
                stage = "TRUST_DEVICE_PROMPT"
                skip = first_visible(page, [
                    "button:has-text('Ahora no')", "a:has-text('Ahora no')",
                    "button:has-text('No ahora')", "a:has-text('No ahora')",
                    "button:has-text('Más tarde')", "a:has-text('Más tarde')",
                    "button:has-text('Omitir')", "a:has-text('Omitir')",
                ])
                final_url = page.url
                if skip is None:
                    ctx.close(); print(status_payload("BLOCKED_TRUST_DEVICE_SKIP_NOT_FOUND", attempts=attempts,
                                                      stage=stage, page_url=final_url)); return 4
                blocked_post_path = ""
                skip.click(timeout=8000, no_wait_after=True)
                page.wait_for_timeout(1800)
                if blocked_post_path:
                    final_url = page.url
                    ctx.close(); print(status_payload("BLOCKED_TRUST_DEVICE_SKIP_ROUTE", attempts=attempts,
                                                      blocked_post_path=blocked_post_path,
                                                      stage=stage, page_url=final_url)); return 4
                if authenticated_url(page.url):
                    ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=attempts)); return 0

            if otp_required(page):
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_2FA_REQUIRED", attempts=attempts,
                                                  stage=stage, page_url=final_url)); return 4

            username = first_visible(page, [
                "input[autocomplete='username']", "input[placeholder*='usuario' i]",
                "input[aria-label*='usuario' i]", "input[name*='user' i]",
                "input[id*='user' i]", "input[type='email']",
            ])
            passwd = first_visible(page, [
                "input[type='password']", "input[autocomplete='current-password']",
                "input[placeholder*='contraseña' i]", "input[aria-label*='contraseña' i]",
                "input[name*='pass' i]", "input[id*='pass' i]",
            ])
            if username is None or passwd is None:
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_LOGIN_FIELDS_NOT_FOUND", attempts=attempts,
                                                  stage=stage, page_url=final_url)); return 4
            try:
                username.fill(user)
                passwd.fill(password)
            except Exception:
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_FIELD_FILL_FAILED", attempts=attempts,
                                                  stage=stage, page_url=final_url)); return 4

            stage = "SUBMIT_LOGIN"
            submit = first_visible(page, [
                "button:has-text('Ingresar')", "button:has-text('Continuar')",
                "button:has-text('Siguiente')", "button:has-text('Validar')",
                "button[type='submit']", "input[type='submit']",
            ])
            if submit is None:
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_LOGIN_SUBMIT_NOT_FOUND", attempts=attempts,
                                                  stage=stage, page_url=final_url)); return 4
            blocked_post_path = ""
            auth_observation = {}
            submit.click(timeout=8000, no_wait_after=True)
            page.wait_for_timeout(3500)

            if blocked_post_path:
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_UNAPPROVED_POST", attempts=attempts,
                                                  blocked_post_path=blocked_post_path,
                                                  stage=stage, page_url=final_url,
                                                  auth_observation=auth_observation)); return 4
            if authenticated_url(page.url):
                ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=attempts,
                                                  auth_observation=auth_observation)); return 0
            if trust_prompt_present(page):
                final_url = page.url
                ctx.close(); print(status_payload("AUTHENTICATED_PENDING_TRUST_DEVICE_PROMPT", attempts=attempts,
                                                  stage="TRUST_DEVICE_PROMPT_AFTER_LOGIN", page_url=final_url,
                                                  auth_observation=auth_observation)); return 3
            if otp_required(page) or auth_observation.get("twofa"):
                final_url = page.url
                ctx.close(); print(status_payload("BLOCKED_AUTH_2FA_REQUIRED", attempts=attempts,
                                                  stage=stage, page_url=final_url,
                                                  auth_observation=auth_observation)); return 4

            classified = classify_auth_observation(auth_observation)
            final_url = page.url
            ctx.close()
            print(status_payload(classified or "BLOCKED_AUTH_SESSION_EXPIRED", attempts=attempts,
                                 blocked_post_path=blocked_post_path,
                                 stage=stage, page_url=final_url,
                                 auth_observation=auth_observation))
            return 0 if classified == "AUTH_API_SUCCESS_NO_TRADING_SESSION" else 4
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED_BROWSER_ERROR",
            "error_type": type(exc).__name__,
            "stage": stage,
            "page_url": clean_url(page.url)[:240] if page is not None else "",
            "blocked_post_path": blocked_post_path[:240],
            "credentials_exposed": False,
            "orders_visited": False,
            "real_orders_sent": 0,
        }, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
