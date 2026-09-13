#!/usr/bin/env python3
"""RC6 PPI Web trusted-profile reauthentication helper.

Security contract:
- credentials come only from the local host secret and are never printed;
- only GET/HEAD/OPTIONS plus the exact PPI login POST are allowed;
- order/trade/cancel paths are never visited;
- all other mutations are aborted;
- OTP/2FA is never automated;
- authentication is GREEN only after a read-only GET reaches Trading without a
  login redirect. A successful Account /cuentas landing is only intermediate.
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
    u = urlsplit(str(value)); return f"{u.scheme}://{u.netloc}{u.path}"


def clean_host_path(value: str) -> str:
    u = urlsplit(str(value)); return f"{u.netloc}{u.path}"[:240]


def safe_page_url(value: str) -> bool:
    u = urlsplit(str(value))
    return u.scheme == "https" and u.netloc in ALLOWED_PAGE_HOSTS and ORDER_PATH_HINT.search(u.path.lower()) is None


def authenticated_trading_url(value: str) -> bool:
    u = urlsplit(str(value))
    return (
        u.scheme == "https" and u.netloc == TRADING_HOST
        and "login" not in u.path.lower() and "logout" not in u.path.lower()
        and ORDER_PATH_HINT.search(u.path.lower()) is None
    )


def authenticated_account_intermediate(value: str) -> bool:
    u = urlsplit(str(value))
    return (
        u.scheme == "https" and u.netloc == ACCOUNT_HOST
        and u.path.rstrip("/").lower() == "/cuentas"
    )


def parse_secret(path: Path) -> tuple[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1); values[k.strip()] = v.strip().strip('"').strip("'")
    return values.get("PPI_WEB_USERNAME", ""), values.get("PPI_WEB_PASSWORD", "")


def body_text(page) -> str:
    try: return page.locator("body").inner_text(timeout=3000)[:20000]
    except Exception: return ""


def first_visible(page, selectors):
    for selector in selectors:
        try:
            q = page.locator(selector)
            for i in range(min(q.count(), 12)):
                if q.nth(i).is_visible(): return q.nth(i)
        except Exception: pass
    return None


def trust_prompt_present(page) -> bool:
    text = body_text(page); return bool(TRUST_PROMPT_HINT.search(text) and NOW_NOT_HINT.search(text))


def otp_required(page) -> bool:
    if trust_prompt_present(page): return False
    if OTP_TEXT_HINT.search(body_text(page)): return True
    return first_visible(page, [
        "input[autocomplete='one-time-code']", "input[name*='pin' i]", "input[id*='pin' i]",
        "input[name*='otp' i]", "input[id*='otp' i]", "input[name*='token' i]",
        "input[id*='token' i]", "input[name*='code' i]", "input[id*='code' i]",
    ]) is not None


def status_payload(status: str, *, attempts=0, blocked_post_path="", stage="", page_url="", auth_observation=None) -> str:
    p = {"status": status, "attempts": attempts, "credentials_exposed": False,
         "orders_visited": False, "real_orders_sent": 0}
    if blocked_post_path: p["blocked_post_path"] = blocked_post_path[:240]
    if stage: p["stage"] = stage[:80]
    if page_url: p["page_url"] = clean_url(page_url)[:240]
    if auth_observation:
        p.update({
            "auth_http_status": auth_observation.get("http_status"),
            "auth_json_object": bool(auth_observation.get("json_object")),
            "auth_has_token_shape": bool(auth_observation.get("has_token")),
            "auth_twofa_shape": bool(auth_observation.get("twofa")),
            "auth_change_password_shape": bool(auth_observation.get("change_password")),
            "auth_safe_keys": auth_observation.get("safe_keys", [])[:20],
        })
    return json.dumps(p, sort_keys=True)


def classify_auth(obs: dict) -> str | None:
    if not obs: return None
    status = int(obs.get("http_status") or 0)
    if obs.get("twofa"): return "BLOCKED_AUTH_2FA_REQUIRED"
    if obs.get("change_password"): return "BLOCKED_AUTH_PASSWORD_CHANGE_REQUIRED"
    if status == 429: return "BLOCKED_AUTH_RATE_LIMITED"
    if status >= 500: return "BLOCKED_AUTH_SERVER_ERROR"
    if status in {400, 401}: return "BLOCKED_AUTH_CREDENTIALS_REJECTED_OR_CHALLENGE"
    if status == 403: return "BLOCKED_AUTH_FORBIDDEN_OR_CHALLENGE"
    if status: return "BLOCKED_AUTH_RESPONSE_UNCLASSIFIED"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True); ap.add_argument("--secret", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args(); profile = Path(args.profile); secret = Path(args.secret)
    if not profile.is_dir(): print(status_payload("BLOCKED_AUTH_PROFILE_MISSING")); return 4
    if not secret.is_file(): print(status_payload("BLOCKED_AUTH_LOCAL_SECRET_MISSING")); return 4
    user, password = parse_secret(secret)
    if not user or not password: print(status_payload("BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE")); return 4
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except Exception:
        print(status_payload("BLOCKED_PLAYWRIGHT_UNAVAILABLE")); return 4

    attempts = 0; blocked_post_path = ""; stage = "START"; page = None; auth_observation = {}
    try:
        with sync_playwright() as pw:
            stage = "LAUNCH_PERSISTENT_CONTEXT"
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000}, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(8000); page.set_default_navigation_timeout(15000)

            def guard(route, request):
                nonlocal blocked_post_path
                method = request.method.upper(); u = urlsplit(request.url)
                if u.scheme != "https": return route.abort()
                if method in {"GET", "HEAD", "OPTIONS"}:
                    if u.netloc in ALLOWED_PAGE_HOSTS and ORDER_PATH_HINT.search(u.path.lower()): return route.abort()
                    return route.continue_()
                key = (u.netloc, u.path.rstrip("/") or "/")
                if method == "POST" and key in APPROVED_AUTH_POSTS: return route.continue_()
                if method == "POST" and u.netloc not in ALLOWED_PAGE_HOSTS: return route.abort()
                blocked_post_path = clean_host_path(request.url); return route.abort()

            def observe(response):
                nonlocal auth_observation
                try:
                    u = urlsplit(response.url); key = (u.netloc, u.path.rstrip("/") or "/")
                    if response.request.method.upper() != "POST" or key not in LOGIN_API_KEYS: return
                    obs = {"http_status": int(response.status), "json_object": False, "has_token": False,
                           "twofa": False, "change_password": False, "safe_keys": []}
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            obs["json_object"] = True
                            safe = {"twoFAInfo","changePassword","token","usuario","user","error","errors","message","status"}
                            obs["safe_keys"] = sorted(k for k in data if k in safe)
                            obs["twofa"] = bool(data.get("twoFAInfo")); obs["change_password"] = bool(data.get("changePassword"))
                            obs["has_token"] = bool(data.get("token") or data.get("accessToken"))
                    except Exception: pass
                    auth_observation = obs
                except Exception: pass

            ctx.route("**/*", guard); page.on("response", observe)

            def goto_fast(url, label):
                nonlocal stage
                stage = label; page.goto(url, wait_until="commit", timeout=15000)
                try: page.wait_for_load_state("domcontentloaded", timeout=7000)
                except PlaywrightTimeoutError: pass
                page.wait_for_timeout(700)

            goto_fast(TRADING_ROOT, "OPEN_TRADING_ROOT")
            if authenticated_trading_url(page.url):
                ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE", attempts=0)); return 0

            goto_fast(LOGIN_URL, "OPEN_LOGIN"); attempts = 1
            if not safe_page_url(page.url):
                final = page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_UNEXPECTED_PAGE", attempts=attempts, stage=stage, page_url=final)); return 4
            if otp_required(page):
                final = page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_2FA_REQUIRED", attempts=attempts, stage=stage, page_url=final)); return 4

            username = first_visible(page, ["input[autocomplete='username']","input[placeholder*='usuario' i]","input[name*='user' i]","input[id*='user' i]","input[type='email']"])
            passwd = first_visible(page, ["input[type='password']","input[autocomplete='current-password']","input[name*='pass' i]","input[id*='pass' i]"])
            submit = first_visible(page, ["button:has-text('Ingresar')","button:has-text('Continuar')","button:has-text('Siguiente')","button:has-text('Validar')","button[type='submit']","input[type='submit']"])
            if username is None or passwd is None: final=page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_LOGIN_FIELDS_NOT_FOUND",attempts=attempts,stage=stage,page_url=final)); return 4
            if submit is None: final=page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_LOGIN_SUBMIT_NOT_FOUND",attempts=attempts,stage=stage,page_url=final)); return 4
            username.fill(user); passwd.fill(password); blocked_post_path = ""; auth_observation = {}; stage = "SUBMIT_LOGIN"
            submit.click(timeout=8000, no_wait_after=True); page.wait_for_timeout(3500)

            if blocked_post_path:
                final=page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_UNAPPROVED_POST",attempts=attempts,blocked_post_path=blocked_post_path,stage=stage,page_url=final,auth_observation=auth_observation)); return 4
            if otp_required(page) or auth_observation.get("twofa"):
                final=page.url; ctx.close(); print(status_payload("BLOCKED_AUTH_2FA_REQUIRED",attempts=attempts,stage=stage,page_url=final,auth_observation=auth_observation)); return 4
            if trust_prompt_present(page):
                skip = first_visible(page,["button:has-text('Ahora no')","a:has-text('Ahora no')","button:has-text('No ahora')","a:has-text('No ahora')","button:has-text('Más tarde')","a:has-text('Más tarde')","button:has-text('Omitir')","a:has-text('Omitir')"])
                if skip is None:
                    final=page.url; ctx.close(); print(status_payload("BLOCKED_TRUST_DEVICE_SKIP_NOT_FOUND",attempts=attempts,stage="TRUST_DEVICE_PROMPT",page_url=final,auth_observation=auth_observation)); return 4
                skip.click(timeout=8000,no_wait_after=True); page.wait_for_timeout(1800)

            if authenticated_trading_url(page.url):
                ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE",attempts=attempts,stage=stage,page_url=page.url,auth_observation=auth_observation)); return 0

            # Current PPI flow lands on Account /cuentas. Treat it only as an
            # intermediate authentication signal, then prove the Trading session
            # by a read-only GET. Never infer GREEN from the API response alone.
            if authenticated_account_intermediate(page.url) and int(auth_observation.get("http_status") or 0) in range(200,300):
                goto_fast(TRADING_ROOT, "VERIFY_TRADING_SESSION")
                if authenticated_trading_url(page.url):
                    final=page.url; ctx.close(); print(status_payload("AUTHENTICATED_TRUSTED_DEVICE",attempts=attempts,stage="VERIFY_TRADING_SESSION",page_url=final,auth_observation=auth_observation)); return 0

            classified = classify_auth(auth_observation) or "BLOCKED_AUTH_SESSION_EXPIRED"
            final=page.url; ctx.close(); print(status_payload(classified,attempts=attempts,blocked_post_path=blocked_post_path,stage=stage,page_url=final,auth_observation=auth_observation)); return 4
    except Exception as exc:
        print(json.dumps({"status":"BLOCKED_BROWSER_ERROR","error_type":type(exc).__name__,"stage":stage,
                          "page_url":clean_url(page.url)[:240] if page is not None else "",
                          "blocked_post_path":blocked_post_path[:240],"credentials_exposed":False,
                          "orders_visited":False,"real_orders_sent":0},sort_keys=True)); return 4


if __name__ == "__main__":
    raise SystemExit(main())
