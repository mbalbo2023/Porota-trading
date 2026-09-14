#!/usr/bin/env python3
"""One-shot, fail-closed PPI Web authentication diagnostic.

Purpose: explain why the trusted-profile reauth loop stays on SUBMIT_LOGIN without
repeating blind login attempts. It performs at most ONE approved login submission.

Safety:
- reads credentials only from a local host secret path;
- never prints credentials, cookies, tokens, request/response bodies, or account data;
- GET/HEAD/OPTIONS are allowed; POST only to explicitly approved authentication endpoints;
- order/trade/confirm/cancel-like paths are always blocked;
- no DB access, no service control, no broker/order API calls;
- OTP/2FA is detected but never automated.
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
ACCOUNT_HOST = "cuenta.portfoliopersonal.com"
TRADING_HOST = "trading.portfoliopersonal.com"
ALLOWED_PAGE_HOSTS = {ACCOUNT_HOST, TRADING_HOST}
APPROVED_AUTH_POSTS = {
    ("cuenta.portfoliopersonal.com", "/login"),
    ("api.portfoliopersonal.com", "/api/Seguridad/Auth/Login"),
    ("trading.portfoliopersonal.com", "/api/logInSSO"),
}
IGNORED_POST_HOSTS = {"px.ads.linkedin.com", "metrics.hotjar.io", "l.clarity.ms"}
ORDER_PATH_HINT = re.compile(r"(^|/)(operar|orden|orders?|trade|confirm|cancel)(/|$)", re.I)
OTP_TEXT_HINT = re.compile(
    r"(pin|otp|token|c[oó]digo).{0,120}(mail|correo|email|verific|seguridad|autentic)|"
    r"(mail|correo|email).{0,120}(pin|otp|token|c[oó]digo)|segundo factor|"
    r"doble factor|autenticaci[oó]n de dos factores",
    re.I | re.S,
)
TRUST_HINT = re.compile(
    r"dispositivo.{0,80}(confianza|confiable|seguro)|"
    r"(confiar|recordar|verificar).{0,80}dispositivo",
    re.I | re.S,
)
GENERIC_ERROR_HINT = re.compile(
    r"(credencial|usuario|contrase[nñ]a).{0,80}(incorrect|inv[aá]lid|error)|"
    r"(incorrect|inv[aá]lid|error).{0,80}(credencial|usuario|contrase[nñ]a)|"
    r"demasiados intentos|bloquead|intente nuevamente|no pudimos ingresar",
    re.I | re.S,
)
SAFE_RESPONSE_KEYS = {
    "twoFAInfo", "changePassword", "token", "accessToken", "usuario", "user",
    "error", "errors", "message", "status", "success", "result", "code",
}


def hp(url: str) -> tuple[str, str]:
    u = urlsplit(url)
    return u.netloc, u.path.rstrip("/") or "/"


def clean_url(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.scheme}://{u.netloc}{u.path}"[:240]


def authenticated_trading_url(url: str) -> bool:
    u = urlsplit(str(url))
    p = u.path.lower()
    return (
        u.scheme == "https"
        and u.netloc == TRADING_HOST
        and bool(u.path.rstrip("/"))
        and "login" not in p
        and "logout" not in p
        and ORDER_PATH_HINT.search(p) is None
    )


def read_secret(path: Path) -> tuple[str, str]:
    vals: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals.get("PPI_WEB_USERNAME", ""), vals.get("PPI_WEB_PASSWORD", "")


def visible(page, selectors):
    for selector in selectors:
        try:
            q = page.locator(selector)
            for i in range(min(q.count(), 12)):
                node = q.nth(i)
                if node.is_visible():
                    return node
        except Exception:
            pass
    return None


def body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2500)[:15000]
    except Exception:
        return ""


def safe_json_shape(data):
    if not isinstance(data, dict):
        return {"json_object": False, "safe_keys": []}
    keys = sorted(k for k in data.keys() if str(k) in SAFE_RESPONSE_KEYS)
    return {
        "json_object": True,
        "safe_keys": keys,
        "has_token_key": bool({"token", "accessToken"} & set(keys)),
        "has_twofa_key": "twoFAInfo" in keys,
        "has_change_password_key": "changePassword" in keys,
        "has_error_key": bool({"error", "errors"} & set(keys)),
        "has_message_key": "message" in keys,
        "has_success_key": "success" in keys,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--secret", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    profile = Path(args.profile)
    secret = Path(args.secret)
    result = {
        "schema": "porota-ppi-auth-diagnostic-oneshot-v2",
        "login_submissions": 0,
        "credentials_exposed": False,
        "cookies_exposed": False,
        "response_bodies_exposed": False,
        "orders_visited": False,
        "real_orders_sent": 0,
        "db_import_executed": False,
        "service_restarted": False,
        "approved_auth_responses": [],
        "blocked_unapproved_post": "",
    }

    if not profile.is_dir() or not secret.is_file():
        result["state"] = "BLOCKED_LOCAL_INPUT_MISSING"
        print(json.dumps(result, sort_keys=True))
        return 4

    user, password = read_secret(secret)
    result["local_secret_has_username"] = bool(user)
    result["local_secret_has_password"] = bool(password)
    if not password:
        result["state"] = "BLOCKED_LOCAL_PASSWORD_MISSING"
        print(json.dumps(result, sort_keys=True))
        return 4

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except Exception:
        result["state"] = "BLOCKED_PLAYWRIGHT_UNAVAILABLE"
        print(json.dumps(result, sort_keys=True))
        return 4

    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(7000)
            page.set_default_navigation_timeout(15000)

            def guard(route, request):
                method = request.method.upper()
                u = urlsplit(request.url)
                if u.scheme != "https":
                    return route.abort()
                if ORDER_PATH_HINT.search(u.path.lower()):
                    result["orders_visited"] = True
                    return route.abort()
                if method in {"GET", "HEAD", "OPTIONS"}:
                    return route.continue_()
                key = (u.netloc, u.path.rstrip("/") or "/")
                if method == "POST" and key in APPROVED_AUTH_POSTS:
                    return route.continue_()
                if method == "POST" and u.netloc in IGNORED_POST_HOSTS:
                    return route.abort()
                result["blocked_unapproved_post"] = f"{u.netloc}{u.path}"[:240]
                return route.abort()

            def observe(response):
                try:
                    req = response.request
                    key = hp(response.url)
                    if req.method.upper() != "POST" or key not in APPROVED_AUTH_POSTS:
                        return
                    item = {"host_path": f"{key[0]}{key[1]}", "http_status": int(response.status)}
                    try:
                        item.update(safe_json_shape(response.json()))
                    except Exception:
                        item.update({"json_object": False, "safe_keys": []})
                    result["approved_auth_responses"].append(item)
                except Exception:
                    pass

            ctx.route("**/*", guard)
            page.on("response", observe)

            def go(url: str):
                try:
                    page.goto(url, wait_until="commit", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=6000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(900)

            go(TRADING_ROOT)
            result["initial_url"] = clean_url(page.url)
            if authenticated_trading_url(page.url):
                result["state"] = "AUTHENTICATED_EXISTING_SESSION"
                result["final_url"] = clean_url(page.url)
                ctx.close()
                print(json.dumps(result, sort_keys=True))
                return 0

            go(LOGIN_URL)
            result["login_page_url"] = clean_url(page.url)
            text_before = body_text(page)
            if OTP_TEXT_HINT.search(text_before):
                result["state"] = "BLOCKED_AUTH_2FA_REQUIRED_BEFORE_SUBMIT"
                result["final_url"] = clean_url(page.url)
                ctx.close(); print(json.dumps(result, sort_keys=True)); return 4

            username = visible(page, [
                "#username", "input[name='username']", "input[autocomplete='username']",
                "input[placeholder*='usuario' i]", "input[aria-label*='usuario' i]",
                "input[name*='user' i]", "input[id*='user' i]", "input[type='email']",
            ])
            passwd = visible(page, [
                "#password", "input[name='password']", "input[type='password']",
                "input[autocomplete='current-password']", "input[placeholder*='contraseña' i]",
                "input[aria-label*='contraseña' i]", "input[name*='pass' i]", "input[id*='pass' i]",
            ])
            submit = visible(page, [
                "button:has-text('Ingresar')", "button:has-text('Continuar')",
                "button:has-text('Siguiente')", "button:has-text('Validar')",
                "button[type='submit']", "input[type='submit']",
            ])
            result["username_field_visible"] = username is not None
            result["password_field_visible"] = passwd is not None
            result["submit_control_visible"] = submit is not None
            result["trust_prompt_before_submit"] = bool(TRUST_HINT.search(text_before))

            if passwd is None or submit is None:
                result["state"] = "BLOCKED_LOGIN_FORM_SHAPE_CHANGED"
                result["final_url"] = clean_url(page.url)
                ctx.close(); print(json.dumps(result, sort_keys=True)); return 4

            if username is not None:
                try:
                    current = (username.input_value(timeout=2000) or "").strip()
                except Exception:
                    current = ""
                result["username_was_prefilled"] = bool(current)
                if not current:
                    if not user:
                        result["state"] = "BLOCKED_USERNAME_REQUIRED_BUT_NOT_STORED"
                        result["final_url"] = clean_url(page.url)
                        ctx.close(); print(json.dumps(result, sort_keys=True)); return 4
                    username.fill(user, timeout=5000)

            passwd.fill(password, timeout=5000)
            result["login_submissions"] = 1
            submit.click(timeout=7000, no_wait_after=True)
            page.wait_for_timeout(5000)

            text_after = body_text(page)
            result["final_url"] = clean_url(page.url)
            fu = urlsplit(page.url)
            result["landed_account_cuentas"] = fu.netloc == ACCOUNT_HOST and fu.path.rstrip("/").lower() == "/cuentas"
            result["landed_trading_nonroot"] = authenticated_trading_url(page.url)
            result["password_field_visible_after"] = visible(page, ["input[type='password']", "#password"]) is not None
            result["otp_shape_after"] = bool(OTP_TEXT_HINT.search(text_after)) or visible(page, [
                "input[autocomplete='one-time-code']", "input[name*='otp' i]", "input[id*='otp' i]",
                "input[name*='pin' i]", "input[id*='pin' i]",
            ]) is not None
            result["trust_prompt_after"] = bool(TRUST_HINT.search(text_after))
            result["generic_login_error_hint_after"] = bool(GENERIC_ERROR_HINT.search(text_after))

            statuses = [int(x.get("http_status") or 0) for x in result["approved_auth_responses"]]
            if result["blocked_unapproved_post"]:
                result["state"] = "BLOCKED_UNAPPROVED_POST"
            elif result["otp_shape_after"]:
                result["state"] = "BLOCKED_AUTH_2FA_REQUIRED_AFTER_SUBMIT"
            elif result["landed_trading_nonroot"]:
                result["state"] = "AUTHENTICATED_TRADING_AFTER_ONE_SUBMIT"
            elif result["landed_account_cuentas"]:
                result["state"] = "AUTH_ACCEPTED_ACCOUNT_LANDING_SSO_NOT_VERIFIED"
            elif any(s == 429 for s in statuses):
                result["state"] = "BLOCKED_AUTH_RATE_LIMITED"
            elif any(s in {400, 401, 403} for s in statuses):
                result["state"] = "BLOCKED_AUTH_REJECTED_OR_CHALLENGE"
            elif result["generic_login_error_hint_after"]:
                result["state"] = "BLOCKED_LOGIN_PAGE_ERROR_HINT"
            elif result["approved_auth_responses"]:
                result["state"] = "AUTH_RESPONSE_OBSERVED_BUT_SESSION_NOT_PROMOTED"
            else:
                result["state"] = "NO_APPROVED_AUTH_RESPONSE_OBSERVED"

            ctx.close()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["state"].startswith("AUTHENTICATED_") else 4
    except Exception as exc:
        result["state"] = "DIAGNOSTIC_EXCEPTION_FAIL_CLOSED"
        result["exception_class"] = exc.__class__.__name__
        print(json.dumps(result, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
