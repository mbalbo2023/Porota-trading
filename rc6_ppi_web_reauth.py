#!/usr/bin/env python3
"""RC6 PPI web trusted-profile reauthentication helper.

Purpose: renew an expired PPI web session using credentials already stored on
host, without exposing them and without visiting trading/order routes.

Safety contract:
- no DB access, broker imports, orders, quantities or prices;
- credentials are read only from a local secret file and never printed/written;
- a trusted profile may require only PPI_WEB_PASSWORD; username is optional;
- navigation is restricted to PPI account/login and trading landing pages;
- POST is permitted only to the exact account /login endpoint;
- the optional trusted-device prompt may be dismissed with "Ahora no" only if
  it does not require an additional unapproved POST route;
- if a real OTP/PIN/2FA code is required, fail closed and never automate email;
- after successful login this helper exits; Contract Evidence collection remains
  a separate GET-only process.
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
ORDER_PATH_HINT = re.compile(r"(^|/)(operar|orden|orders?|trade|confirm|cancel)(/|$)", re.I)
OTP_INPUT_HINT = re.compile(r"(pin|otp|token|one[-_ ]?time|verification[-_ ]?code|codigo|c[oó]digo)", re.I)
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
    # Optional trusted-device enrollment is explicitly NOT a mandatory OTP.
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


def status_payload(status: str, *, attempts: int = 0, blocked_post_path: str = "") -> str:
    payload = {
        "status": status,
        "attempts": attempts,
        "credentials_exposed": False,
        "orders_visited": False,
        "real_orders_sent": 0,
    }
    if blocked_post_path:
        payload["blocked_post_path"] = blocked_post_path[:240]
    return json.dumps(payload, sort_keys=True)


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
    if not password:
        print(status_payload("BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE")); return 4
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print(status_payload("BLOCKED_PLAYWRIGHT_UNAVAILABLE")); return 4

    attempts = 0
    blocked_post_path = ""
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            # Authentication helper is default-deny for mutations. Exact /login
            # is the only permitted POST. GET/HEAD/OPTIONS assets may load over
            # HTTPS so the login UI can function. Any other POST is blocked and
            # only its sanitized host/path is retained for diagnosis.
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
                if method == "POST" and u.netloc == "cuenta.portfoliopersonal.com" and u.path.rstrip("/") == "/login":
                    return route.continue_()
                blocked_post_path = f"{u.netloc}{u.path}"[:240]
                return route.abort()

            ctx.route("**/*", guard)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(TRADING_ROOT, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(800)
            if authenticated_url(page.url):
                ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=0)); return 0

            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(800)
            for attempts in range(1, 6):
                if not safe_page_url(page.url):
                    ctx.close(); print(status_payload("BLOCKED_AUTH_UNEXPECTED_PAGE", attempts=attempts)); return 4
                if authenticated_url(page.url):
                    ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=attempts)); return 0

                if trust_prompt_present(page):
                    skip = first_visible(page, [
                        "button:has-text('Ahora no')", "a:has-text('Ahora no')",
                        "button:has-text('No ahora')", "a:has-text('No ahora')",
                        "button:has-text('Más tarde')", "a:has-text('Más tarde')",
                        "button:has-text('Omitir')", "a:has-text('Omitir')",
                    ])
                    if skip is None:
                        ctx.close(); print(status_payload("BLOCKED_TRUST_DEVICE_SKIP_NOT_FOUND", attempts=attempts)); return 4
                    blocked_post_path = ""
                    skip.click(); page.wait_for_timeout(1800)
                    if blocked_post_path:
                        ctx.close(); print(status_payload("BLOCKED_TRUST_DEVICE_SKIP_ROUTE", attempts=attempts,
                                                          blocked_post_path=blocked_post_path)); return 4
                    continue

                if otp_required(page):
                    ctx.close(); print(status_payload("BLOCKED_AUTH_2FA_REQUIRED", attempts=attempts)); return 4

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
                acted = False
                if username is not None:
                    if not user:
                        ctx.close(); print(status_payload("BLOCKED_AUTH_USERNAME_REQUIRED", attempts=attempts)); return 4
                    try:
                        if not username.input_value():
                            username.fill(user)
                        acted = True
                    except Exception:
                        pass
                if passwd is not None:
                    try:
                        passwd.fill(password); acted = True
                    except Exception:
                        pass
                if acted:
                    submit = first_visible(page, [
                        "button:has-text('Ingresar')", "button:has-text('Continuar')",
                        "button:has-text('Siguiente')", "button:has-text('Validar')",
                        "button[type='submit']", "input[type='submit']",
                    ])
                    if submit is None:
                        ctx.close(); print(status_payload("BLOCKED_AUTH_LOGIN_SUBMIT_NOT_FOUND", attempts=attempts)); return 4
                    blocked_post_path = ""
                    submit.click(); page.wait_for_timeout(2500)
                    if blocked_post_path:
                        ctx.close(); print(status_payload("BLOCKED_AUTH_UNAPPROVED_POST", attempts=attempts,
                                                          blocked_post_path=blocked_post_path)); return 4
                    continue

                back = first_visible(page, [
                    "button:has-text('Cambiar usuario')", "a:has-text('Cambiar usuario')",
                    "button:has-text('Volver')", "a:has-text('Volver')",
                ])
                if back is not None:
                    back.click(); page.wait_for_timeout(1000); continue
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(800)

            ok = authenticated_url(page.url)
            ctx.close()
            print(status_payload("AUTHENTICATED_TRUSTED_DEVICE" if ok else "BLOCKED_AUTH_SESSION_EXPIRED",
                                 attempts=attempts, blocked_post_path=blocked_post_path))
            return 0 if ok else 4
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_BROWSER_ERROR", "error_type": type(exc).__name__,
                          "credentials_exposed": False, "orders_visited": False,
                          "real_orders_sent": 0}, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())